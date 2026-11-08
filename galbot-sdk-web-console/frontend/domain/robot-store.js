export class RobotStore extends EventTarget {
  constructor() {
    super();
    this.state = { jointState: null, localCloud: null, sensors: null, controller: null };
  }

  update(next) {
    this.state = { ...this.state, ...next };
    this.dispatchEvent(new CustomEvent('change', { detail: this.state }));
  }

  consume(event) {
    if (event.type === 'joint_state') this.update({ jointState: event.data });
    if (event.type === 'local_cloud') this.update({ localCloud: event.data });
    if (event.type === 'robot_sensors') this.update({ sensors: event.data });
  }
}
