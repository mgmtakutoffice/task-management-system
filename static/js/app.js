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

// Searchable Client selector on Add Task and authorised-user Update Task forms.
// The original <select name="client_code"> remains the value submitted to Flask.
const clientSelect = document.querySelector('[data-client-search]');

if (clientSelect && !clientSelect.disabled) {
  const originalOptions = Array.from(clientSelect.options)
    .filter((option) => option.value)
    .map((option) => ({
      value: option.value,
      text: option.textContent.replace(/\s+/g, ' ').trim(),
    }));

  const wrapper = document.createElement('div');
  wrapper.className = 'client-search';

  const input = document.createElement('input');
  input.type = 'search';
  input.className = 'client-search-input';
  input.placeholder = 'Type client code or name...';
  input.autocomplete = 'off';
  input.setAttribute('aria-label', 'Search client');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-expanded', 'false');

  const results = document.createElement('div');
  results.className = 'client-search-results';
  results.setAttribute('role', 'listbox');
  results.hidden = true;

  clientSelect.parentNode.insertBefore(wrapper, clientSelect);
  wrapper.appendChild(input);
  wrapper.appendChild(results);
  wrapper.appendChild(clientSelect);

  // Keep the real select for form submission, but use the visible search box
  // for required-field validation once JavaScript enhancement is active.
  clientSelect.classList.add('client-search-native');
  clientSelect.required = false;
  input.required = true;

  const selectedOption = clientSelect.options[clientSelect.selectedIndex];
  if (selectedOption && selectedOption.value) {
    input.value = selectedOption.textContent.replace(/\s+/g, ' ').trim();
  }

  const closeResults = () => {
    results.hidden = true;
    input.setAttribute('aria-expanded', 'false');
  };

  const chooseClient = (value, text) => {
    clientSelect.value = value;
    input.value = text;
    input.setCustomValidity('');
    closeResults();
    clientSelect.dispatchEvent(new Event('change', { bubbles: true }));
  };

  const renderResults = (searchText = '') => {
    const term = searchText.trim().toLowerCase();
    const matches = originalOptions.filter((option) =>
      option.text.toLowerCase().includes(term),
    );

    results.innerHTML = '';

    if (!matches.length) {
      const empty = document.createElement('div');
      empty.className = 'client-search-empty';
      empty.textContent = 'No matching client found';
      results.appendChild(empty);
    } else {
      matches.forEach((option) => {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'client-search-option';
        button.setAttribute('role', 'option');
        button.textContent = option.text;
        button.addEventListener('click', () => {
          chooseClient(option.value, option.text);
        });
        results.appendChild(button);
      });
    }

    results.hidden = false;
    input.setAttribute('aria-expanded', 'true');
  };

  input.addEventListener('focus', () => {
    renderResults(input.value);
  });

  input.addEventListener('input', () => {
    // Once the displayed text changes, require a fresh selection from the
    // matching list so a free-typed client name cannot be submitted.
    clientSelect.value = '';
    input.setCustomValidity('');
    renderResults(input.value);
  });

  input.addEventListener('keydown', (event) => {
    const options = Array.from(results.querySelectorAll('.client-search-option'));

    if (event.key === 'ArrowDown' && options.length) {
      event.preventDefault();
      options[0].focus();
    } else if (event.key === 'Enter' && options.length && !results.hidden) {
      event.preventDefault();
      options[0].click();
      input.focus();
    } else if (event.key === 'Escape') {
      closeResults();
    }
  });

  results.addEventListener('keydown', (event) => {
    const options = Array.from(results.querySelectorAll('.client-search-option'));
    const currentIndex = options.indexOf(document.activeElement);

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      const nextIndex = currentIndex < options.length - 1 ? currentIndex + 1 : 0;
      options[nextIndex]?.focus();
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      if (currentIndex <= 0) {
        input.focus();
      } else {
        options[currentIndex - 1]?.focus();
      }
    } else if (event.key === 'Escape') {
      closeResults();
      input.focus();
    }
  });

  document.addEventListener('click', (event) => {
    if (!wrapper.contains(event.target)) closeResults();
  });

  const taskForm = clientSelect.closest('form');
  if (taskForm) {
    taskForm.addEventListener('submit', (event) => {
      if (!clientSelect.value) {
        event.preventDefault();
        event.stopPropagation();
        input.setCustomValidity('Please select a client from the search results.');
        input.reportValidity();
        input.focus();
      } else {
        input.setCustomValidity('');
      }
    });
  }
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

// Task notifications: browser Web Push first, in-app bell/history always,
// and a slower polling fallback when Web Push is unavailable or disabled.
const notificationRoot = document.querySelector('[data-notification-root]');

if (notificationRoot) {
  const notificationBell = notificationRoot.querySelector('[data-notification-bell]');
  const notificationCount = notificationRoot.querySelector('[data-notification-count]');
  const notificationPanel = notificationRoot.querySelector('[data-notification-panel]');
  const notificationPanelCount = notificationRoot.querySelector('[data-notification-panel-count]');
  const notificationList = notificationRoot.querySelector('[data-notification-list]');
  const notificationToasts = document.querySelector('[data-notification-toasts]');
  const pushControls = notificationRoot.querySelector('[data-push-controls]');
  const pushEnableButton = notificationRoot.querySelector('[data-push-enable]');
  const pushStatus = notificationRoot.querySelector('[data-push-status]');
  const logoutLink = document.querySelector('[data-logout-link]');

  const notificationUser = notificationRoot.dataset.notificationUser || 'user';
  const pushConfigured = notificationRoot.dataset.pushConfigured === 'true';
  const vapidPublicKey = notificationRoot.dataset.vapidPublicKey || '';
  const shownStorageKey = `task-manager-shown-notifications:${notificationUser}`;

  let notificationFetchInFlight = false;
  let audioContext = null;
  let soundUnlocked = false;
  let pushActive = false;
  let notificationPollTimer = null;

  const pushSupported = Boolean(
    pushConfigured
    && vapidPublicKey
    && window.isSecureContext
    && 'serviceWorker' in navigator
    && 'PushManager' in window
    && 'Notification' in window,
  );

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
      // Sound is only a fallback when browser push is not enabled.
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

  const fetchNotifications = async ({ showToasts = !pushActive } = {}) => {
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

      // Do not duplicate an OS/browser push with an in-app toast. Toast + sound
      // remain as a fallback when browser push is unavailable or not enabled.
      if (showToasts && !pushActive) {
        const unseen = notifications.filter(
          (notification) => notification.id && !shownNotificationIds.has(notification.id),
        );

        unseen.slice(0, 3).forEach((notification) => {
          rememberShownNotification(notification.id);
          showNotificationToast(notification);
        });

        if (unseen.length) playNotificationSound();
      }
    } catch (error) {
      // The next fallback cycle or service-worker push message will retry.
    } finally {
      notificationFetchInFlight = false;
    }
  };

  const urlBase64ToUint8Array = (base64String) => {
    const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
    const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
    const rawData = window.atob(base64);
    return Uint8Array.from([...rawData].map((character) => character.charCodeAt(0)));
  };

  const setPushStatus = (message, { showButton = false } = {}) => {
    if (pushControls) pushControls.hidden = false;
    if (pushStatus) pushStatus.textContent = message || '';
    if (pushEnableButton) pushEnableButton.hidden = !showButton;
  };

  const savePushSubscription = async (subscription) => {
    const response = await fetch('/api/push/subscribe', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        'X-Requested-With': 'XMLHttpRequest',
      },
      body: JSON.stringify({ subscription: subscription.toJSON() }),
    });
    if (!response.ok) throw new Error('Unable to save browser push subscription.');
  };

  const initializeBrowserPush = async ({ requestPermission = false } = {}) => {
    pushActive = false;

    if (!pushConfigured) {
      if (pushControls) pushControls.hidden = true;
      return false;
    }

    if (!window.isSecureContext) {
      setPushStatus('Browser push requires HTTPS or localhost.');
      return false;
    }

    if (!pushSupported) {
      setPushStatus('Browser push is not supported in this browser.');
      return false;
    }

    try {
      await navigator.serviceWorker.register('/service-worker.js', { scope: '/' });
      const registration = await navigator.serviceWorker.ready;

      let permission = Notification.permission;
      if (permission === 'default' && requestPermission) {
        permission = await Notification.requestPermission();
      }

      if (permission === 'default') {
        setPushStatus('Enable browser notifications for immediate task alerts.', {
          showButton: true,
        });
        return false;
      }

      if (permission !== 'granted') {
        setPushStatus('Browser notifications are blocked in browser settings.');
        return false;
      }

      let subscription = await registration.pushManager.getSubscription();
      if (!subscription) {
        subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(vapidPublicKey),
        });
      }

      await savePushSubscription(subscription);
      pushActive = true;
      setPushStatus('Browser notifications enabled.');
      return true;
    } catch (error) {
      setPushStatus('Browser notifications could not be enabled.');
      return false;
    }
  };

  if (pushEnableButton) {
    pushEnableButton.addEventListener('click', async () => {
      pushEnableButton.disabled = true;
      try {
        await initializeBrowserPush({ requestPermission: true });
        await fetchNotifications({ showToasts: false });
      } finally {
        pushEnableButton.disabled = false;
      }
    });
  }

  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.addEventListener('message', (event) => {
      if (event.data?.type === 'TASK_NOTIFICATION_RECEIVED') {
        fetchNotifications({ showToasts: false });
      }
    });
  }

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

  const scheduleNotificationPoll = () => {
    if (notificationPollTimer) window.clearTimeout(notificationPollTimer);
    const intervalMs = pushActive ? 300000 : 60000; // 5 min with push; 1 min fallback.
    notificationPollTimer = window.setTimeout(async () => {
      if (document.visibilityState === 'visible') {
        await fetchNotifications({ showToasts: !pushActive });
      }
      scheduleNotificationPoll();
    }, intervalMs);
  };

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      fetchNotifications({ showToasts: false });
    }
  });

  // On shared office computers, remove the browser's push endpoint from the
  // current login on Sign out. Notification permission stays granted, so the
  // next user can be auto-subscribed to their own account after login.
  if (logoutLink && 'serviceWorker' in navigator) {
    logoutLink.addEventListener('click', async (event) => {
      event.preventDefault();
      const destination = logoutLink.href;
      const forceNavigate = window.setTimeout(() => {
        window.location.href = destination;
      }, 1600);

      try {
        const registration = await navigator.serviceWorker.getRegistration('/');
        const subscription = registration
          ? await registration.pushManager.getSubscription()
          : null;

        if (subscription) {
          await fetch('/api/push/unsubscribe', {
            method: 'POST',
            credentials: 'same-origin',
            headers: {
              'Content-Type': 'application/json',
              'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify({ endpoint: subscription.endpoint }),
          });
          await subscription.unsubscribe();
        }
      } catch (error) {
        // Sign-out must continue even if push cleanup fails.
      } finally {
        window.clearTimeout(forceNavigate);
        window.location.href = destination;
      }
    });
  }

  (async () => {
    await initializeBrowserPush();
    await fetchNotifications({ showToasts: !pushActive });
    scheduleNotificationPoll();
  })();
}
