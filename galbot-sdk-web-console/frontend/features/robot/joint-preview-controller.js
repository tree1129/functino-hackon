const CORE_JOINT_NAMES = [
  'leg_joint1', 'leg_joint2', 'leg_joint3', 'leg_joint4', 'leg_joint5',
  'head_joint1', 'head_joint2',
  'left_arm_joint1', 'left_arm_joint2', 'left_arm_joint3', 'left_arm_joint4', 'left_arm_joint5', 'left_arm_joint6', 'left_arm_joint7',
  'right_arm_joint1', 'right_arm_joint2', 'right_arm_joint3', 'right_arm_joint4', 'right_arm_joint5', 'right_arm_joint6', 'right_arm_joint7',
];

const JOINT_GROUPS = {
  leg: ['leg_joint1', 'leg_joint2', 'leg_joint3', 'leg_joint4', 'leg_joint5'],
  head: ['head_joint1', 'head_joint2'],
  left_arm: ['left_arm_joint1', 'left_arm_joint2', 'left_arm_joint3', 'left_arm_joint4', 'left_arm_joint5', 'left_arm_joint6', 'left_arm_joint7'],
  right_arm: ['right_arm_joint1', 'right_arm_joint2', 'right_arm_joint3', 'right_arm_joint4', 'right_arm_joint5', 'right_arm_joint6', 'right_arm_joint7'],
};

const UPRIGHT_LEG_JOINT_NAMES = ['leg_joint1', 'leg_joint2', 'leg_joint3'];

function clamp(value, lower, upper) {
  return Math.min(Math.max(value, lower), upper);
}

export class JointPreviewController {
  constructor({ scene, container, renderStatus }) {
    this.scene = scene;
    this.container = container;
    this.renderStatus = renderStatus;
    this.values = new Map();
    this.legUprightMode = false;
    this.selectedGroup = 'head';
  }

  setSelectedGroup(group) {
    if (!Object.hasOwn(JOINT_GROUPS, group)) return;
    this.selectedGroup = group;
    this.build();
  }

  getSelectedJointNames() { return JOINT_GROUPS[this.selectedGroup]; }

  build() {
    const all = new Map(this.scene.getMovableJoints().map((joint) => [joint.name, joint]));
    const joints = this.getSelectedJointNames().map((name) => all.get(name)).filter(Boolean);
    this.container.replaceChildren();
    if (!joints.length) {
      this.container.textContent = '等待机器人模型加载';
      return;
    }
    for (const joint of joints) {
      if (this.legUprightMode && ['leg_joint1', 'leg_joint3'].includes(joint.name)) continue;
      this.container.append(this.createRow(joint, all));
    }
  }

  createRow(joint, all) {
    const row = document.createElement('label');
    row.className = 'joint-row';
    const title = document.createElement('span');
    const uprightControl = this.legUprightMode && joint.name === 'leg_joint2';
    const bounds = uprightControl ? this.getUprightLeg2Bounds(all) : joint;
    title.textContent = uprightControl ? 'leg_joint2（直立）' : joint.name;
    title.title = uprightControl
      ? `leg_joint1:leg_joint2:leg_joint3 = 1:3:2；${bounds.lower.toFixed(3)} 至 ${bounds.upper.toFixed(3)} rad`
      : `${joint.lower.toFixed(3)} 至 ${joint.upper.toFixed(3)} rad`;
    const input = document.createElement('input');
    input.type = 'range';
    input.min = String(bounds.lower);
    input.max = String(bounds.upper);
    input.step = '0.01';
    const initial = clamp(this.values.get(joint.name) ?? 0, bounds.lower, bounds.upper);
    input.value = String(initial);
    const value = document.createElement('output');
    value.textContent = Number(initial).toFixed(2);
    input.addEventListener('input', () => {
      const position = Number(input.value);
      this.values.set(joint.name, position);
      value.textContent = position.toFixed(2);
      if (uprightControl) {
        this.applyUprightLegPosition(position);
      } else {
        this.scene.setJointState({ names: [joint.name], position: [position] });
      }
      this.renderStatus('关节预览仅更新浏览器模型');
    });
    row.append(title, input, value);
    return row;
  }

  getUprightLeg2Bounds(all) {
    const [joint1, joint2, joint3] = UPRIGHT_LEG_JOINT_NAMES.map((name) => all.get(name));
    if (!joint1 || !joint2 || !joint3) return joint2 || { lower: 0, upper: 0 };
    return {
      lower: Math.max(joint1.lower * 3, joint2.lower, joint3.lower * 3 / 2),
      upper: Math.min(joint1.upper * 3, joint2.upper, joint3.upper * 3 / 2),
    };
  }

  applyUprightLegPosition(leg2Position) {
    const positions = [leg2Position / 3, leg2Position, leg2Position * 2 / 3];
    UPRIGHT_LEG_JOINT_NAMES.forEach((name, index) => this.values.set(name, positions[index]));
    this.scene.setJointState({ names: UPRIGHT_LEG_JOINT_NAMES, position: positions });
  }

  setLegUprightMode(enabled) {
    this.legUprightMode = enabled;
    if (enabled) {
      const all = new Map(this.scene.getMovableJoints().map((joint) => [joint.name, joint]));
      const bounds = this.getUprightLeg2Bounds(all);
      this.applyUprightLegPosition(clamp(this.values.get('leg_joint2') ?? 0, bounds.lower, bounds.upper));
    }
    this.build();
    this.renderStatus(enabled ? '腿部直立模型：leg_joint2 联动前三个腿部关节' : '腿部直立模型已关闭');
  }

  syncFromJointState(jointState) {
    for (const joint of jointState) {
      if (CORE_JOINT_NAMES.includes(joint.name) && Number.isFinite(joint.position)) this.values.set(joint.name, joint.position);
    }
    this.build();
  }

  reset() {
    const joints = this.scene.getMovableJoints().filter((joint) => this.getSelectedJointNames().includes(joint.name));
    joints.forEach((joint) => this.values.delete(joint.name));
    this.scene.setJointState({ names: joints.map((joint) => joint.name), position: joints.map((joint) => Math.min(Math.max(0, joint.lower), joint.upper)) });
    this.build();
    this.renderStatus(`已重置 ${joints.length} 个 ${this.selectedGroup} 关节预览`);
  }

  getState() {
    const joints = this.scene.getMovableJoints().filter((joint) => this.getSelectedJointNames().includes(joint.name));
    return {
      names: joints.map((joint) => joint.name),
      position: joints.map((joint) => clamp(this.values.get(joint.name) ?? 0, joint.lower, joint.upper)),
    };
  }
}
