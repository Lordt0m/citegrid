/**
 * CiteGrid — Explore Interface Progressive Enhancement
 * 
 * Provides instantaneous year scrubbing, chart point & table pinning,
 * URL replaceState synchronization, and popstate restoration.
 * Form submissions for filter changes remain native GET navigation.
 */
document.addEventListener('DOMContentLoaded', function () {
  document.body.classList.add('js-active');

  const filterForm = document.getElementById('explore-filter-form');
  const slider = document.getElementById('year-range-slider');
  const yearDisplay = document.getElementById('lens-year-display');
  const crosshair = document.getElementById('chart-crosshair');
  const hiddenYearInput = document.getElementById('hidden-year-input');
  const svgRoot = document.getElementById('explore-svg-root');

  // Auto-submit filter form on indicator change
  if (filterForm) {
    const indicatorRadios = filterForm.querySelectorAll('input[name="indicator"]');
    indicatorRadios.forEach(function (radio) {
      radio.addEventListener('change', function () {
        filterForm.submit();
      });
    });

    const countryCheckboxes = filterForm.querySelectorAll('input[name="countries"]');
    countryCheckboxes.forEach(function (cb) {
      cb.addEventListener('change', function () {
        filterForm.submit();
      });
    });
  }

  // Pre-index table observation values by year and country
  const observationData = {};
  const tableRows = document.querySelectorAll('.citegrid-table tbody tr.table-row');
  tableRows.forEach(function (row) {
    const yr = parseInt(row.getAttribute('data-year'), 10);
    if (!yr) return;

    observationData[yr] = {};
    const cells = row.querySelectorAll('td.table-val-cell');
    cells.forEach(function (cell) {
      const country = cell.getAttribute('data-label');
      const missing = cell.querySelector('.missing-badge');
      const val = missing ? 'No data in this snapshot' : cell.textContent.trim();
      if (country) {
        observationData[yr][country] = {
          value: val,
          isMissing: !!missing
        };
      }
    });
  });

  // Calculate year to X position mapping from SVG year labels
  const yearXMap = {};
  if (svgRoot) {
    const axisLabels = svgRoot.querySelectorAll('.axis-layer text.axis-label');
    axisLabels.forEach(function (lbl) {
      const yr = parseInt(lbl.textContent.trim(), 10);
      const x = parseFloat(lbl.getAttribute('x'));
      if (yr && !isNaN(x)) {
        yearXMap[yr] = x;
      }
    });
  }

  // Minimum and maximum years from slider
  const minYear = slider ? parseInt(slider.min, 10) : 2000;
  const maxYear = slider ? parseInt(slider.max, 10) : 2024;

  function getXForYear(yr) {
    if (yearXMap[yr] !== undefined) {
      return yearXMap[yr];
    }
    if (maxYear === minYear) return 390.0;
    return 60.0 + ((yr - minYear) / (maxYear - minYear)) * 650.0;
  }

  /**
   * Updates Year Lens, chart crosshair, and table highlights for a chosen year.
   */
  function selectYear(year, updateUrl) {
    year = parseInt(year, 10);
    if (isNaN(year) || year < minYear || year > maxYear) return;

    // 1. Update year lens title
    if (yearDisplay) {
      yearDisplay.textContent = year;
    }
    if (hiddenYearInput) {
      hiddenYearInput.value = year;
    }

    // 2. Update slider value
    if (slider && parseInt(slider.value, 10) !== year) {
      slider.value = year;
    }

    // 3. Move chart crosshair
    if (crosshair) {
      const xPos = getXForYear(year);
      crosshair.setAttribute('x1', xPos.toFixed(1));
      crosshair.setAttribute('x2', xPos.toFixed(1));
    }

    // 4. Update Year Lens country values
    const dataForYear = observationData[year] || {};
    const countryRows = document.querySelectorAll('.lens-country-row');
    countryRows.forEach(function (cRow) {
      const cMeta = cRow.querySelector('.country-name');
      const cName = cMeta ? cMeta.textContent.trim() : null;
      const valEl = cRow.querySelector('.lens-value');

      if (cName && valEl && dataForYear[cName]) {
        const item = dataForYear[cName];
        valEl.textContent = item.value;
        if (item.isMissing) {
          valEl.classList.add('missing-val');
        } else {
          valEl.classList.remove('missing-val');
        }
      }
    });

    // 5. Update previous and next button state & links
    const prevBtn = document.getElementById('lens-prev-btn');
    const nextBtn = document.getElementById('lens-next-btn');

    if (prevBtn) {
      if (year > minYear) {
        prevBtn.classList.remove('disabled');
        prevBtn.removeAttribute('aria-disabled');
        prevBtn.textContent = '← ' + (year - 1);
        prevBtn.setAttribute('data-target-year', year - 1);
      } else {
        prevBtn.classList.add('disabled');
        prevBtn.setAttribute('aria-disabled', 'true');
        prevBtn.textContent = '←';
      }
    }

    if (nextBtn) {
      if (year < maxYear) {
        nextBtn.classList.remove('disabled');
        nextBtn.removeAttribute('aria-disabled');
        nextBtn.textContent = (year + 1) + ' →';
        nextBtn.setAttribute('data-target-year', year + 1);
      } else {
        nextBtn.classList.add('disabled');
        nextBtn.setAttribute('aria-disabled', 'true');
        nextBtn.textContent = '→';
      }
    }

    // 6. Highlight active table row
    tableRows.forEach(function (row) {
      const rYr = parseInt(row.getAttribute('data-year'), 10);
      if (rYr === year) {
        row.classList.add('row-selected');
      } else {
        row.classList.remove('row-selected');
      }
    });

    // 7. Update URL query parameter via replaceState
    if (updateUrl && window.history && window.history.replaceState) {
      const currentUrl = new URL(window.location.href);
      currentUrl.searchParams.set('year', year);
      window.history.replaceState({ year: year }, '', currentUrl.toString());
    }
  }

  // Range slider input listener (scrubbing)
  if (slider) {
    slider.addEventListener('input', function () {
      selectYear(this.value, true);
    });
  }

  // Previous and Next year button click listeners
  const prevBtn = document.getElementById('lens-prev-btn');
  if (prevBtn) {
    prevBtn.addEventListener('click', function (e) {
      const targetYear = this.getAttribute('data-target-year');
      if (targetYear) {
        e.preventDefault();
        selectYear(targetYear, true);
      }
    });
  }

  const nextBtn = document.getElementById('lens-next-btn');
  if (nextBtn) {
    nextBtn.addEventListener('click', function (e) {
      const targetYear = this.getAttribute('data-target-year');
      if (targetYear) {
        e.preventDefault();
        selectYear(targetYear, true);
      }
    });
  }

  // SVG Chart data point click and keyboard activation
  const pointTargets = document.querySelectorAll('.chart-point-target');
  pointTargets.forEach(function (target) {
    const yr = target.getAttribute('data-year');
    if (!yr) return;

    target.addEventListener('click', function () {
      selectYear(yr, true);
    });

    target.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        selectYear(yr, true);
      }
    });
  });

  // Table Year Pin Link activation
  const yearPinLinks = document.querySelectorAll('.year-pin-link');
  yearPinLinks.forEach(function (link) {
    const yr = link.getAttribute('data-year');
    if (!yr) return;

    link.addEventListener('click', function (e) {
      e.preventDefault();
      selectYear(yr, true);
    });
  });

  // Browser back/forward navigation restoration
  window.addEventListener('popstate', function () {
    const urlParams = new URLSearchParams(window.location.search);
    const yr = urlParams.get('year');
    if (yr) {
      selectYear(yr, false);
    }
  });
});
