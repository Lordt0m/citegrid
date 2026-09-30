"""
CiteGrid Core Domain Models.

Enforces transactional imports, immutable snapshots, precise decimal values,
and single-latest publication constraints.
"""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from core.provenance import VALID_INDICATORS

VALID_COUNTRIES: list[str] = ['GHA', 'KEN', 'NGA']


class ImportStatus(models.TextChoices):
    STARTED = 'STARTED', 'Started'
    COMPLETED = 'COMPLETED', 'Completed'
    FAILED = 'FAILED', 'Failed'


class PublicationPointer(models.Model):
    """
    Singleton publication lock row used to serialize concurrent snapshot publications.
    Acquired with select_for_update() inside publication transactions.
    """
    name = models.CharField(max_length=50, unique=True, default='wdi_publication')
    locked_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"PublicationPointer({self.name})"


class Snapshot(models.Model):
    """
    An immutable, complete version of the fixed 9-series evidence scope.
    Identified by a monotonic sequence version and a canonical content hash.
    """
    version = models.PositiveIntegerField(
        unique=True,
        help_text="Monotonically increasing sequence number for this publication.",
    )
    content_hash = models.CharField(
        max_length=64,
        db_index=True,
        help_text="SHA-256 of canonical normalized observations and provenance.",
    )
    retrieved_at = models.DateTimeField(
        help_text="Timestamp when CiteGrid fetched the provider response.",
    )
    published_at = models.DateTimeField(
        auto_now_add=True,
        help_text="Timestamp when this snapshot was committed into the ledger.",
    )
    source_id = models.CharField(max_length=10, default='2')
    source_name = models.CharField(
        max_length=100,
        default='World Development Indicators',
    )
    scope_countries = models.JSONField(
        default=list,
        help_text="Fixed list of economies covered (['NGA', 'GHA', 'KEN']).",
    )
    scope_indicators = models.JSONField(
        default=list,
        help_text="Fixed list of indicators covered.",
    )
    scope_start_year = models.IntegerField(default=2000)
    scope_end_year = models.IntegerField()
    is_latest = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True if this is the currently active latest published snapshot.",
    )

    class Meta:
        ordering = ['-version']
        constraints = [
            models.UniqueConstraint(
                fields=['is_latest'],
                condition=models.Q(is_latest=True),
                name='unique_latest_published_snapshot',
            )
        ]

    def __str__(self):
        return f"Snapshot(v{self.version}, hash={self.content_hash[:8]}, latest={self.is_latest})"


class SnapshotValue(models.Model):
    """
    One immutable (snapshot, source, country, indicator, year) observation record.
    Preserves exact decimal precision and authoritative indicator provenance.
    """
    snapshot = models.ForeignKey(
        Snapshot,
        on_delete=models.CASCADE,
        related_name='values',
    )
    source_id = models.CharField(max_length=10, default='2')
    country_code = models.CharField(max_length=3)
    country_name = models.CharField(max_length=100)
    indicator_code = models.CharField(max_length=50)
    indicator_name = models.CharField(max_length=255)
    observation_year = models.IntegerField()
    numeric_value = models.DecimalField(
        max_digits=16,
        decimal_places=8,
        null=True,
        blank=True,
        help_text="Lossless normalized numeric observation, or null if missing.",
    )
    raw_value_str = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Exact string representation returned by provider before parsing.",
    )
    footnote = models.TextField(blank=True, default='')
    unit = models.CharField(max_length=50, blank=True, default='')
    obs_status = models.CharField(max_length=50, blank=True, default='')

    # Authoritative provenance metadata stored with each observation
    indicator_definition = models.TextField()
    provider_label = models.CharField(max_length=100, default='World Bank')
    source_url = models.URLField(max_length=500)
    license = models.CharField(max_length=100, default='CC BY 4.0')
    underlying_source = models.CharField(
        max_length=255,
        default='SDG 7.1.1 electrification dataset',
    )

    class Meta:
        ordering = ['country_code', 'indicator_code', 'observation_year']
        constraints = [
            models.UniqueConstraint(
                fields=['snapshot', 'source_id', 'country_code', 'indicator_code', 'observation_year'],
                name='unique_snapshot_observation',
            )
        ]

    def __str__(self):
        val = f"{self.numeric_value}%" if self.numeric_value is not None else "No data"
        return f"{self.country_code} {self.indicator_code} ({self.observation_year}): {val}"


class ImportRun(models.Model):
    """
    Audit log of one import attempt with validation outcomes, URLs, and timing.
    Durable across transaction failures.
    """
    status = models.CharField(
        max_length=20,
        choices=ImportStatus.choices,
        default=ImportStatus.STARTED,
    )
    started_at = models.DateTimeField(auto_now_add=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    request_urls = models.JSONField(default=list)
    page_metadata = models.JSONField(default=list)
    pages_fetched = models.IntegerField(default=0)
    records_count = models.IntegerField(default=0)
    error_category = models.CharField(max_length=50, null=True, blank=True)
    error_detail = models.TextField(null=True, blank=True)
    attempted_url = models.CharField(max_length=500, null=True, blank=True)
    normalized_content_hash = models.CharField(max_length=64, null=True, blank=True)
    snapshot = models.ForeignKey(
        Snapshot,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='import_runs',
    )
    is_simulation = models.BooleanField(
        default=False,
        help_text="True if imported from a simulated fixture or test scenario.",
    )

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f"ImportRun(id={self.pk}, status={self.status}, records={self.records_count})"


class RevisionTransition(models.TextChoices):
    NEW_VALUE = 'NEW_VALUE', 'New value'
    CHANGED = 'CHANGED', 'Changed'
    WITHDRAWN = 'WITHDRAWN', 'Withdrawn'


class Revision(models.Model):
    """
    Immutable audit record of an observation transition between adjacent complete snapshots.
    Preserves paired old and new provenance and foreign keys to stored SnapshotValue records.
    """
    previous_snapshot = models.ForeignKey(
        Snapshot,
        on_delete=models.CASCADE,
        related_name='outgoing_revisions',
        help_text="Earlier snapshot in the adjacent comparison.",
    )
    current_snapshot = models.ForeignKey(
        Snapshot,
        on_delete=models.CASCADE,
        related_name='incoming_revisions',
        help_text="Later snapshot in the adjacent comparison.",
    )
    old_snapshot_value = models.ForeignKey(
        SnapshotValue,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='revisions_as_old',
        help_text="Reference to the earlier snapshot value record, if present.",
    )
    new_snapshot_value = models.ForeignKey(
        SnapshotValue,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='revisions_as_new',
        help_text="Reference to the later snapshot value record, if present.",
    )

    source_id = models.CharField(max_length=10, default='2')
    country_code = models.CharField(max_length=3)
    country_name = models.CharField(max_length=100)
    indicator_code = models.CharField(max_length=50)
    indicator_name = models.CharField(max_length=255)
    observation_year = models.IntegerField()
    transition_type = models.CharField(
        max_length=20,
        choices=RevisionTransition.choices,
    )

    # Paired old/new values
    old_value = models.DecimalField(max_digits=16, decimal_places=8, null=True, blank=True)
    new_value = models.DecimalField(max_digits=16, decimal_places=8, null=True, blank=True)
    old_raw_value_str = models.CharField(max_length=50, null=True, blank=True)
    new_raw_value_str = models.CharField(max_length=50, null=True, blank=True)
    old_footnote = models.TextField(blank=True, default='')
    new_footnote = models.TextField(blank=True, default='')

    # Paired old/new provenance metadata
    old_unit = models.CharField(max_length=50, blank=True, default='')
    new_unit = models.CharField(max_length=50, blank=True, default='')
    old_indicator_definition = models.TextField(blank=True, default='')
    new_indicator_definition = models.TextField(blank=True, default='')
    old_source_url = models.URLField(max_length=500, blank=True, default='')
    new_source_url = models.URLField(max_length=500, blank=True, default='')
    old_provider_label = models.CharField(max_length=100, blank=True, default='')
    new_provider_label = models.CharField(max_length=100, blank=True, default='')
    old_license = models.CharField(max_length=100, blank=True, default='')
    new_license = models.CharField(max_length=100, blank=True, default='')
    old_underlying_source = models.CharField(max_length=255, blank=True, default='')
    new_underlying_source = models.CharField(max_length=255, blank=True, default='')

    # Retrieval timestamps
    previous_retrieved_at = models.DateTimeField()
    current_retrieved_at = models.DateTimeField()
    detected_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-current_snapshot__version', 'country_code', 'indicator_code', '-observation_year']
        constraints = [
            models.UniqueConstraint(
                fields=['previous_snapshot', 'current_snapshot', 'source_id', 'country_code', 'indicator_code', 'observation_year'],
                name='unique_snapshot_revision_key',
            )
        ]

    def __str__(self):
        old_val = f"{self.old_value}%" if self.old_value is not None else "No data"
        new_val = f"{self.new_value}%" if self.new_value is not None else "No data"
        return (
            f"Revision({self.transition_type}: {self.country_code} {self.indicator_code} "
            f"{self.observation_year} [{old_val} -> {new_val}] v{self.previous_snapshot.version}->v{self.current_snapshot.version})"
        )


class SavedComparison(models.Model):
    """
    A comparison configuration pinned immutably to a specific Snapshot.
    Its briefing display and export continue to read that exact snapshot even
    after subsequent complete snapshots are published.
    """
    title = models.CharField(max_length=200)
    snapshot = models.ForeignKey(
        Snapshot,
        on_delete=models.PROTECT,
        related_name='saved_comparisons',
        help_text="The immutable snapshot to which this comparison is pinned.",
    )
    indicator_code = models.CharField(
        max_length=50,
        help_text="Indicator code compared across countries.",
    )
    countries = models.JSONField(
        default=list,
        help_text="Canonically sorted, non-empty list of ISO3 economy codes.",
    )
    start_year = models.IntegerField(help_text="Inclusive start observation year.")
    end_year = models.IntegerField(help_text="Inclusive end observation year.")
    is_public_example = models.BooleanField(
        default=False,
        db_index=True,
        help_text="Designates the single public read-only example comparison.",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='saved_comparisons',
        help_text="Optional analyst owner if authentication is enabled.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['is_public_example'],
                condition=models.Q(is_public_example=True),
                name='unique_single_public_example',
            ),
        ]

    def clean(self):
        super().clean()
        if not self.title or not self.title.strip():
            raise ValidationError({"title": "Title cannot be empty."})
        self.title = self.title.strip()

        if not self.snapshot_id:
            raise ValidationError({"snapshot": "Pinned snapshot is required."})

        # Validate indicator code against allowlist
        if self.indicator_code not in VALID_INDICATORS:
            raise ValidationError({"indicator_code": f"Invalid indicator '{self.indicator_code}'."})
        
        # Validate indicator exists in snapshot
        if not self.snapshot.values.filter(indicator_code=self.indicator_code).exists():
            raise ValidationError({
                "indicator_code": f"Indicator '{self.indicator_code}' does not exist in Snapshot v{self.snapshot.version}."
            })

        # Validate countries
        if not isinstance(self.countries, list) or not self.countries:
            raise ValidationError({"countries": "Countries must be a non-empty list."})
        invalid_countries = [c for c in self.countries if c not in VALID_COUNTRIES]
        if invalid_countries:
            raise ValidationError({"countries": f"Invalid country codes: {invalid_countries}"})

        # Canonicalize & deduplicate: keep unique and sort alphabetically
        self.countries = sorted(list(dict.fromkeys(self.countries)))

        # Validate all countries exist in snapshot for this indicator
        stored_countries = set(
            self.snapshot.values.filter(indicator_code=self.indicator_code).values_list('country_code', flat=True).distinct()
        )
        missing_from_snapshot = [c for c in self.countries if c not in stored_countries]
        if missing_from_snapshot:
            raise ValidationError({
                "countries": f"Countries {missing_from_snapshot} do not exist in Snapshot v{self.snapshot.version}."
            })

        # Validate year bounds against snapshot scope
        if self.start_year > self.end_year:
            raise ValidationError({
                "start_year": f"start_year ({self.start_year}) cannot be greater than end_year ({self.end_year})."
            })
        if self.start_year < self.snapshot.scope_start_year:
            raise ValidationError({
                "start_year": f"start_year ({self.start_year}) is earlier than snapshot scope start ({self.snapshot.scope_start_year})."
            })
        if self.end_year > self.snapshot.scope_end_year:
            raise ValidationError({
                "end_year": f"end_year ({self.end_year}) exceeds snapshot scope end ({self.snapshot.scope_end_year})."
            })

        # Model-layer check for single public example
        if self.is_public_example:
            qs = SavedComparison.objects.filter(is_public_example=True)
            if self.pk:
                qs = qs.exclude(pk=self.pk)
            if qs.exists():
                raise ValidationError({"is_public_example": "A public example comparison already exists."})

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"SavedComparison({self.title} [v{self.snapshot.version}] public={self.is_public_example})"
