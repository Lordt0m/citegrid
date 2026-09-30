/**
 * CiteGrid Replay Progressive Enhancement.
 * 
 * Provides keyboard shortcut navigation (ArrowLeft / ArrowRight) for stepping
 * through the 4-step replay sequence.
 * 
 * Invariants:
 * - Zero autoplay, zero artificial tickers, zero fake live streaming.
 * - Works 100% without JavaScript via native links.
 */
document.addEventListener('DOMContentLoaded', () => {
  // Arrow key navigation between replay steps
  document.addEventListener('keydown', (e) => {
    // Ignore if focus is in an input, select, or textarea
    if (['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
      return;
    }

    if (e.key === 'ArrowRight') {
      const nextBtn = document.querySelector('a[href*="step="].btn-primary');
      if (nextBtn) {
        nextBtn.click();
      }
    } else if (e.key === 'ArrowLeft') {
      const prevBtn = document.querySelector('a[href*="step="].btn-secondary');
      if (prevBtn) {
        prevBtn.click();
      }
    }
  });
});
