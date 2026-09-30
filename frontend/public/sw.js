// Shows alerts as system notifications, delivered by Web Push from the
// server (backend/alerts/push.py), whether or not the app is open.

self.addEventListener('install', () => self.skipWaiting())
self.addEventListener('activate', (event) => event.waitUntil(self.clients.claim()))

self.addEventListener('push', (event) => {
  let alert = { title: 'Trade desk alert', body: '', url: '/alerts?tab=log', tag: '' }
  try {
    alert = { ...alert, ...event.data.json() }
  } catch {
    if (event.data) alert.body = event.data.text()
  }
  event.waitUntil(
    self.registration.showNotification(alert.title, {
      body: alert.body,
      tag: alert.tag || undefined,
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      data: { url: alert.url },
      // stays on screen until dismissed, where the platform allows it
      requireInteraction: true,
    }),
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()
  const url = (event.notification.data && event.notification.data.url) || '/'
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      for (const w of windows) {
        if (new URL(w.url).origin === self.location.origin) {
          w.navigate(url)
          return w.focus()
        }
      }
      return self.clients.openWindow(url)
    }),
  )
})
