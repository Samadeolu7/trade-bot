import { client, unwrap } from '../api/client'

export type PushSupport = 'ok' | 'unsupported' | 'install-first'

/** Registers the service worker that shows pushed alerts (public/sw.js). */
export function registerServiceWorker() {
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/sw.js').catch(() => {
      /* notifications just won't be offered */
    })
  }
}

export function pushSupport(): PushSupport {
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent)
  const installed = window.matchMedia('(display-mode: standalone)').matches
  // iOS only allows web push for sites added to the Home Screen
  if (ios && !installed) return 'install-first'
  if (!('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) return 'unsupported'
  return 'ok'
}

function keyBytes(base64url: string): Uint8Array<ArrayBuffer> {
  const padded = (base64url + '='.repeat((4 - (base64url.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/')
  const raw = atob(padded)
  const bytes = new Uint8Array(new ArrayBuffer(raw.length))
  for (let i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i)
  return bytes
}

export async function currentSubscription(): Promise<PushSubscription | null> {
  if (pushSupport() !== 'ok') return null
  const registration = await navigator.serviceWorker.ready
  return registration.pushManager.getSubscription()
}

/** Asks permission, subscribes this browser and tells the server where to push. */
export async function enablePush(publicKey: string): Promise<void> {
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') {
    throw new Error(
      permission === 'denied'
        ? 'Notifications are blocked for this site. Allow them in the browser’s site settings, then try again.'
        : 'Notifications weren’t allowed.',
    )
  }
  const registration = await navigator.serviceWorker.ready
  let subscription = await registration.pushManager.getSubscription()
  if (!subscription) {
    try {
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: keyBytes(publicKey),
      })
    } catch (err) {
      throw new Error(
        `This browser wouldn’t set up notifications (${err instanceof Error ? err.message : String(err)}). ` +
          'Private windows and some privacy settings block them; try a normal window.',
      )
    }
  }
  const json = subscription.toJSON()
  await unwrap(
    client.POST('/api/alerts/push/subscribe', {
      body: { endpoint: json.endpoint!, p256dh: json.keys!.p256dh, auth: json.keys!.auth },
    }),
  )
}

export async function disablePush(): Promise<void> {
  const subscription = await currentSubscription()
  if (!subscription) return
  await unwrap(client.POST('/api/alerts/push/unsubscribe', { body: { endpoint: subscription.endpoint } }))
  await subscription.unsubscribe()
}
