'use strict';

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (error) {
    payload = {
      title: 'Task Manager',
      message: event.data ? event.data.text() : 'You have a new task notification.',
      open_url: '/',
    };
  }

  const title = payload.title || 'Task Manager';
  const options = {
    body: payload.message || 'You have a new task notification.',
    tag: payload.notification_id
      ? `task-notification-${payload.notification_id}`
      : `task-notification-${Date.now()}`,
    data: {
      open_url: payload.open_url || '/',
      notification_id: payload.notification_id || '',
      task_id: payload.task_id || '',
      type: payload.type || '',
    },
  };

  event.waitUntil(
    Promise.all([
      self.registration.showNotification(title, options),
      self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
        clients.forEach((client) => {
          client.postMessage({ type: 'TASK_NOTIFICATION_RECEIVED' });
        });
      }),
    ]),
  );
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();

  const relativeUrl = event.notification?.data?.open_url || '/';
  const targetUrl = new URL(relativeUrl, self.location.origin).href;

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(async (clients) => {
      for (const client of clients) {
        if ('navigate' in client) {
          try {
            const navigated = await client.navigate(targetUrl);
            if (navigated && 'focus' in navigated) return navigated.focus();
          } catch (error) {
            // Try another existing client or fall back to opening a new window.
          }
        }
      }

      if (self.clients.openWindow) return self.clients.openWindow(targetUrl);
      return undefined;
    }),
  );
});
