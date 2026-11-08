export class NavigationStore extends EventTarget {
  constructor() { super(); this.state = { connected: false, localized: false, pose: null, status: 'IDLE', task_id: null }; }
  update(next) {
    const changedTask = Object.hasOwn(next, 'task_id') && next.task_id !== this.state.task_id;
    this.state = { ...this.state, ...next, ...(changedTask ? { path: null } : {}) };
    this.dispatchEvent(new CustomEvent('change', { detail: this.state }));
  }
  consume(event) {
    if (event.type === 'navigation_state') this.update(event.data);
    if (event.type === 'navigation_path') {
      if (event.data?.task_id && this.state.task_id && event.data.task_id !== this.state.task_id) return;
      this.update({ path: event.data });
    }
  }
  get pose() { return this.state.pose; }
}
