function ensureOutbox(kernel) {
  if (!Array.isArray(kernel.personLifecycleOutbox)) kernel.personLifecycleOutbox = [];
  return kernel.personLifecycleOutbox;
}

export function enqueuePersonLifecycleEvent(kernel, event, deliveryStatus = 'pending') {
  const outbox = ensureOutbox(kernel);
  const existing = outbox.find((item) => item.event_id === event.event_id);
  if (existing) return { eventId: existing.event_id, status: existing.delivery_status, idempotent: true };
  const item = {
    ...event,
    delivery_status: deliveryStatus,
    attempts: 0,
    created_at: new Date().toISOString(),
    last_error: null,
    delivered_at: null
  };
  outbox.push(item);
  return { eventId: item.event_id, status: item.delivery_status, idempotent: false };
}

export function updatePersonLifecycleOutbox(kernel, eventId, patch = {}) {
  const item = ensureOutbox(kernel).find((entry) => entry.event_id === eventId);
  if (!item) return null;
  Object.assign(item, patch);
  return item;
}

export function pendingPersonLifecycleEvents(kernel) {
  return ensureOutbox(kernel).filter((item) => ['pending', 'scheduled', 'retrying'].includes(item.delivery_status));
}
