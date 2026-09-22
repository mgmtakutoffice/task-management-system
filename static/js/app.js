const toggle = document.querySelector('[data-menu-toggle]');
const sidebar = document.querySelector('[data-sidebar]');
if (toggle && sidebar) {
  toggle.addEventListener('click', () => sidebar.classList.toggle('open'));
}

const statusSelect = document.querySelector('[data-status-select]');
const completionWrap = document.querySelector('[data-completion-wrap]');
const checkerWrap = document.querySelector('[data-checker-wrap]');
const checkerSelect = document.querySelector('[data-checker-select]');

if (statusSelect && completionWrap) {
  const completionInput = completionWrap.querySelector('input');
  const updateCompletion = () => {
    const completed = statusSelect.value.toLowerCase() === 'completed';
    completionWrap.classList.toggle('muted-field', !completed);
    completionInput.disabled = !completed;
    if (completed && !completionInput.value) {
      completionInput.value = new Date().toISOString().slice(0, 10);
    }
    if (!completed) completionInput.value = '';
  };
  statusSelect.addEventListener('change', updateCompletion);
  updateCompletion();
}

if (statusSelect && checkerWrap && checkerSelect) {
  const updateChecker = () => {
    const pendingChecking = statusSelect.value.toLowerCase() === 'pending for checking';
    checkerWrap.classList.toggle('muted-field', !pendingChecking);
    checkerSelect.required = pendingChecking;
  };
  statusSelect.addEventListener('change', updateChecker);
  updateChecker();
}

setTimeout(() => {
  document.querySelectorAll('.flash').forEach((element) => element.classList.add('fade-out'));
}, 5000);

// Prevent accidental double submission of Save / Approve / Reject forms.
document.addEventListener('submit', (event) => {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;

  if (form.dataset.submitting === 'true') {
    event.preventDefault();
    return;
  }

  form.dataset.submitting = 'true';

  // Preserve the clicked submit button's name/value before disabling it.
  // Disabled submit controls are not included in the form POST, which matters
  // for forms where the submit button itself selects the server-side action
  // (for example checking_action=complete / assign_next).
  const submitter = event.submitter;
  if (
    (submitter instanceof HTMLButtonElement
      || submitter instanceof HTMLInputElement)
    && submitter.name
  ) {
    let preservedSubmitter = form.querySelector(
      'input[type="hidden"][data-preserved-submitter="true"]',
    );
    if (!(preservedSubmitter instanceof HTMLInputElement)) {
      preservedSubmitter = document.createElement('input');
      preservedSubmitter.type = 'hidden';
      preservedSubmitter.dataset.preservedSubmitter = 'true';
      form.appendChild(preservedSubmitter);
    }
    preservedSubmitter.name = submitter.name;
    preservedSubmitter.value = submitter.value;
  }

  form.querySelectorAll('button[type="submit"], input[type="submit"]').forEach((button) => {
    button.disabled = true;

    if (button instanceof HTMLButtonElement) {
      button.dataset.originalText = button.textContent || '';
      button.textContent = 'Please wait…';
    }
  });
});

// Ignore the second rapid click on the same internal navigation link.
let lastNavigationHref = '';
let lastNavigationAt = 0;

document.addEventListener('click', (event) => {
  if (
    event.defaultPrevented
    || event.button !== 0
    || event.ctrlKey
    || event.metaKey
    || event.shiftKey
    || event.altKey
  ) {
    return;
  }

  const target = event.target;
  if (!(target instanceof Element)) return;

  const link = target.closest('a[href]');
  if (!(link instanceof HTMLAnchorElement)) return;
  if (link.target || link.hasAttribute('download')) return;

  const url = new URL(link.href, window.location.href);
  if (url.origin !== window.location.origin) return;

  const now = Date.now();
  if (lastNavigationHref === url.href && now - lastNavigationAt < 1200) {
    event.preventDefault();
    return;
  }

  lastNavigationHref = url.href;
  lastNavigationAt = now;
});

// In-app task notifications: bell, unread count, toasts, sound and 30-second polling.
const notificationRoot = document.querySelector('[data-notification-root]');

if (notificationRoot) {
  const notificationBell = notificationRoot.querySelector('[data-notification-bell]');
  const notificationCount = notificationRoot.querySelector('[data-notification-count]');
  const notificationPanel = notificationRoot.querySelector('[data-notification-panel]');
  const notificationPanelCount = notificationRoot.querySelector('[data-notification-panel-count]');
  const notificationList = notificationRoot.querySelector('[data-notification-list]');
  const notificationToasts = document.querySelector('[data-notification-toasts]');
  const notificationUser = notificationRoot.dataset.notificationUser || 'user';
  const shownStorageKey = `task-manager-shown-notifications:${notificationUser}`;

  let notificationFetchInFlight = false;
  let audioContext = null;
  let soundUnlocked = false;

  const loadShownNotificationIds = () => {
    try {
      const stored = JSON.parse(sessionStorage.getItem(shownStorageKey) || '[]');
      return new Set(Array.isArray(stored) ? stored : []);
    } catch (error) {
      return new Set();
    }
  };

  const shownNotificationIds = loadShownNotificationIds();

  const rememberShownNotification = (notificationId) => {
    if (!notificationId) return;
    shownNotificationIds.add(notificationId);
    const recentIds = Array.from(shownNotificationIds).slice(-150);
    try {
      sessionStorage.setItem(shownStorageKey, JSON.stringify(recentIds));
    } catch (error) {
      // sessionStorage may be unavailable in a restrictive browser mode.
    }
  };

  const unlockNotificationSound = async () => {
    try {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return;
      if (!audioContext) audioContext = new AudioContextClass();
      if (audioContext.state === 'suspended') await audioContext.resume();
      soundUnlocked = audioContext.state === 'running';
    } catch (error) {
      soundUnlocked = false;
    }
  };

  const playNotificationSound = () => {
    if (!soundUnlocked || !audioContext) return;
    try {
      const oscillator = audioContext.createOscillator();
      const gain = audioContext.createGain();
      oscillator.type = 'sine';
      oscillator.frequency.setValueAtTime(720, audioContext.currentTime);
      oscillator.frequency.exponentialRampToValueAtTime(
        980,
        audioContext.currentTime + 0.12,
      );
      gain.gain.setValueAtTime(0.0001, audioContext.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.08, audioContext.currentTime + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + 0.18);
      oscillator.connect(gain);
      gain.connect(audioContext.destination);
      oscillator.start();
      oscillator.stop(audioContext.currentTime + 0.2);
    } catch (error) {
      // Sound is optional; the visual notification remains available.
    }
  };

  document.addEventListener('pointerdown', unlockNotificationSound, { once: true });
  document.addEventListener('keydown', unlockNotificationSound, { once: true });

  const setUnreadCount = (count) => {
    if (!notificationCount || !notificationPanelCount) return;
    const numericCount = Number.isFinite(Number(count)) ? Number(count) : 0;
    notificationCount.textContent = numericCount > 99 ? '99+' : String(numericCount);
    notificationCount.hidden = numericCount <= 0;
    notificationPanelCount.textContent = `${numericCount} unread`;
  };

  const createNotificationText = (tagName, className, text) => {
    const element = document.createElement(tagName);
    element.className = className;
    element.textContent = text || '';
    return element;
  };

  const markNotificationRead = async (notification, rowElement = null) => {
    if (!notification.read_url) return;
    try {
      const response = await fetch(notification.read_url, {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        credentials: 'same-origin',
      });
      if (!response.ok) return;
      if (rowElement) rowElement.remove();
      await fetchNotifications({ showToasts: false });
    } catch (error) {
      // Keep the notification visible if marking read fails.
    }
  };

  const renderNotificationList = (notifications) => {
    if (!notificationList) return;
    notificationList.replaceChildren();

    if (!notifications.length) {
      notificationList.appendChild(
        createNotificationText('div', 'notification-empty', 'No unread notifications.'),
      );
      return;
    }

    notifications.forEach((notification) => {
      const row = document.createElement('div');
      row.className = 'notification-item';

      const link = document.createElement('a');
      link.className = 'notification-item-link';
      link.href = notification.open_url || '#';

      const title = createNotificationText(
        'strong',
        'notification-item-title',
        notification.title || 'Notification',
      );
      const message = createNotificationText(
        'span',
        'notification-item-message',
        notification.message || '',
      );
      const meta = createNotificationText(
        'small',
        'notification-item-meta',
        notification.created_at || '',
      );

      link.append(title, message, meta);

      const readButton = document.createElement('button');
      readButton.type = 'button';
      readButton.className = 'notification-read-button';
      readButton.textContent = '✓';
      readButton.title = 'Mark notification read';
      readButton.setAttribute('aria-label', 'Mark notification read');
      readButton.addEventListener('click', (event) => {
        event.preventDefault();
        event.stopPropagation();
        markNotificationRead(notification, row);
      });

      row.append(link, readButton);
      notificationList.appendChild(row);
    });
  };

  const showNotificationToast = (notification) => {
    if (!notificationToasts) return;

    const toast = document.createElement('div');
    toast.className = 'notification-toast';

    const link = document.createElement('a');
    link.className = 'notification-toast-link';
    link.href = notification.open_url || '#';

    const title = createNotificationText(
      'strong',
      'notification-toast-title',
      notification.title || 'Task notification',
    );
    const message = createNotificationText(
      'span',
      'notification-toast-message',
      notification.message || '',
    );
    const meta = createNotificationText(
      'small',
      'notification-toast-meta',
      notification.created_at || '',
    );

    link.append(title, message, meta);

    const closeButton = document.createElement('button');
    closeButton.type = 'button';
    closeButton.className = 'notification-toast-close';
    closeButton.textContent = '×';
    closeButton.setAttribute('aria-label', 'Dismiss notification popup');
    closeButton.addEventListener('click', () => toast.remove());

    toast.append(link, closeButton);
    notificationToasts.appendChild(toast);

    window.setTimeout(() => {
      toast.classList.add('notification-toast-hide');
      window.setTimeout(() => toast.remove(), 300);
    }, 9000);
  };

  const fetchNotifications = async ({ showToasts = true } = {}) => {
    if (notificationFetchInFlight) return;
    notificationFetchInFlight = true;
    try {
      const response = await fetch('/api/notifications/unread', {
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest' },
        cache: 'no-store',
      });
      if (!response.ok) return;

      const payload = await response.json();
      const notifications = Array.isArray(payload.notifications)
        ? payload.notifications
        : [];
      setUnreadCount(payload.count || 0);
      renderNotificationList(notifications);

      if (showToasts) {
        const unseen = notifications.filter(
          (notification) => notification.id && !shownNotificationIds.has(notification.id),
        );

        unseen.slice(0, 3).forEach((notification) => {
          rememberShownNotification(notification.id);
          showNotificationToast(notification);
        });

        // One short tone per polling cycle, even when several notifications arrive.
        if (unseen.length) playNotificationSound();
      }
    } catch (error) {
      // Polling failures are intentionally silent; the next 30-second cycle retries.
    } finally {
      notificationFetchInFlight = false;
    }
  };

  if (notificationBell && notificationPanel) {
    notificationBell.addEventListener('click', (event) => {
      event.stopPropagation();
      const opening = notificationPanel.hidden;
      notificationPanel.hidden = !opening;
      notificationBell.setAttribute('aria-expanded', opening ? 'true' : 'false');
      if (opening) fetchNotifications({ showToasts: false });
    });

    document.addEventListener('click', (event) => {
      if (notificationPanel.hidden) return;
      const target = event.target;
      if (target instanceof Node && !notificationRoot.contains(target)) {
        notificationPanel.hidden = true;
        notificationBell.setAttribute('aria-expanded', 'false');
      }
    });

    document.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && !notificationPanel.hidden) {
        notificationPanel.hidden = true;
        notificationBell.setAttribute('aria-expanded', 'false');
      }
    });
  }

  // Fetch immediately after login/page navigation, then every 30 seconds.
  fetchNotifications();
  window.setInterval(() => {
    if (document.visibilityState === 'visible') fetchNotifications();
  }, 30000);

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') fetchNotifications();
  });
}
