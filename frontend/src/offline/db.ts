/**
 * The device's offline store: IndexedDB, one database PER SHOP AND PERSON (`shop-offline:<shop>:<user>`).
 *
 * Two stores: `queue` (operations waiting to be synced) and `snapshots` (the read-only copies of products and customers, each stamped with
 * when the server made it). Nothing else is kept: no password, no session token, no balances, no cost or profit. IndexedDB is not a vault
 * (anyone with access to the unlocked device and browser profile can read it), so the data kept is deliberately small, is removed when the
 * person signs out (the queue only if they choose to keep it, see `signOutCleanup`), and is never read for another shop or person because
 * the database name carries both.
 */

export interface Scope {
  shopId: number
  userId: number
}

const PREFIX = 'shop-offline:'
const INDEX_KEY = 'shop-offline-databases' // names of the databases this browser made, for browsers without indexedDB.databases()
export const dbName = (s: Scope) => `${PREFIX}${s.shopId}:${s.userId}`

function remember(name: string): void {
  try {
    const known = new Set<string>(JSON.parse(localStorage.getItem(INDEX_KEY) ?? '[]'))
    known.add(name)
    localStorage.setItem(INDEX_KEY, JSON.stringify([...known]))
  } catch {
    // Storage may be unavailable: the databases() listing is used where it exists.
  }
}

export function openDb(scope: Scope): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const name = dbName(scope)
    const request = indexedDB.open(name, 1)
    request.onupgradeneeded = () => {
      const db = request.result
      db.createObjectStore('queue', { keyPath: 'clientOpId' })
      db.createObjectStore('snapshots', { keyPath: 'name' })
    }
    request.onsuccess = () => {
      remember(name)
      resolve(request.result)
    }
    request.onerror = () => reject(request.error ?? new Error('Offline storage is not available.'))
  })
}

function wrap<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
}

export async function getAll<T>(scope: Scope, store: 'queue' | 'snapshots'): Promise<T[]> {
  const db = await openDb(scope)
  try {
    return await wrap<T[]>(db.transaction(store).objectStore(store).getAll())
  } finally {
    db.close()
  }
}

export async function getOne<T>(scope: Scope, store: 'queue' | 'snapshots', key: string): Promise<T | undefined> {
  const db = await openDb(scope)
  try {
    return await wrap<T | undefined>(db.transaction(store).objectStore(store).get(key))
  } finally {
    db.close()
  }
}

export async function putOne<T>(scope: Scope, store: 'queue' | 'snapshots', value: T): Promise<void> {
  const db = await openDb(scope)
  try {
    await wrap(db.transaction(store, 'readwrite').objectStore(store).put(value))
  } finally {
    db.close()
  }
}

export async function removeOne(scope: Scope, store: 'queue' | 'snapshots', key: string): Promise<void> {
  const db = await openDb(scope)
  try {
    await wrap(db.transaction(store, 'readwrite').objectStore(store).delete(key))
  } finally {
    db.close()
  }
}

export async function clearStore(scope: Scope, store: 'queue' | 'snapshots'): Promise<void> {
  const db = await openDb(scope)
  try {
    await wrap(db.transaction(store, 'readwrite').objectStore(store).clear())
  } finally {
    db.close()
  }
}

function deleteDb(name: string): Promise<void> {
  return new Promise((resolve) => {
    const request = indexedDB.deleteDatabase(name)
    request.onsuccess = () => resolve()
    request.onerror = () => resolve()
    request.onblocked = () => resolve()
  })
}

/** Every offline database this browser holds for this app, whichever shop or person it belongs to. */
export async function allDatabaseNames(): Promise<string[]> {
  const names = new Set<string>()
  try {
    for (const info of (await indexedDB.databases?.()) ?? []) if (info.name?.startsWith(PREFIX)) names.add(info.name)
  } catch {
    // databases() is not available everywhere.
  }
  try {
    for (const name of JSON.parse(localStorage.getItem(INDEX_KEY) ?? '[]') as string[]) names.add(name)
  } catch {
    // ignore
  }
  return [...names]
}

/** Delete this person's offline database entirely. */
export async function deleteScope(scope: Scope): Promise<void> {
  const name = dbName(scope)
  await deleteDb(name)
  try {
    const rest = (JSON.parse(localStorage.getItem(INDEX_KEY) ?? '[]') as string[]).filter((n) => n !== name)
    localStorage.setItem(INDEX_KEY, JSON.stringify(rest))
  } catch {
    // ignore
  }
}

/** Delete the read copies (products, customers) of one person, keeping any unsynced operations. */
export const clearSnapshots = (scope: Scope) => clearStore(scope, 'snapshots')

/** Delete EVERY offline database on this device (used by "remove all offline data"). */
export async function deleteAllScopes(): Promise<void> {
  await Promise.all((await allDatabaseNames()).map(deleteDb))
  try {
    localStorage.removeItem(INDEX_KEY)
  } catch {
    // ignore
  }
}
