/**
 * CiteGrid Revisions Ledger Progressive Enhancement.
 * Provides keyboard shortcut (Escape closes inspection drawer) and auto-submitting filters.
 */
document.addEventListener('DOMContentLoaded', function () {
  // Auto-submit filter form on select change
  const filterForm = document.querySelector('.revisions-filter-form');
  if (filterForm) {
    const selects = filterForm.querySelectorAll('select');
    selects.forEach(function (sel) {
      sel.addEventListener('change', function () {
        filterForm.submit();
      });
    });
  }

  // Keyboard navigation: Escape key closes inspection drawer
  const drawer = document.getElementById('inspection-drawer');
  if (drawer) {
    // Scroll drawer into view gently if opened via query param
    drawer.scrollIntoView({ behavior: 'smooth', block: 'nearest' });

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' || e.key === 'Esc') {
        const closeBtn = drawer.querySelector('.inspection-header a.btn');
        if (closeBtn) {
          closeBtn.click();
        }
      }
    });
  }
});
