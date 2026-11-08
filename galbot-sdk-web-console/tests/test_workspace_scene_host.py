import base64
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="Node.js is required for frontend lifecycle tests")
def test_workspace_scene_host_lifecycle_and_failure_cleanup() -> None:
    source = (ROOT / "frontend/scene/workspace-scene-host.js").read_bytes()
    module_url = "data:text/javascript;base64," + base64.b64encode(source).decode("ascii")
    script = f"""
      import assert from 'node:assert/strict';
      const {{ WorkspaceSceneHost }} = await import('{module_url}');
      const calls = [];
      const scene = {{
        mode: 'goal',
        cancelWorldPointCollection() {{ calls.push('clear:points'); }},
        setMotionObstacleEditingEnabled(value) {{ calls.push(`clear:gizmo:${{value}}`); }},
        enableMotionObstacleEditing(value) {{ calls.push(`clear:callback:${{value}}`); }},
        setMode(value) {{ this.mode = value; calls.push(`mode:${{value}}`); }},
      }};
      const host = new WorkspaceSceneHost(scene);
      let disposed = 0;
      host.register('robot', {{
        activate() {{ calls.push('robot:activate'); }},
        deactivate() {{ calls.push('robot:deactivate'); }},
        dispose() {{ disposed += 1; }},
      }});
      host.register('motion', {{
        activate() {{ calls.push('motion:activate'); }},
        deactivate() {{ calls.push('motion:deactivate'); }},
        dispose() {{ disposed += 1; }},
      }});
      host.register('broken', {{ activate() {{ throw new Error('broken profile'); }} }});

      host.activate('robot');
      host.activate('robot');
      assert.equal(calls.filter((item) => item === 'robot:activate').length, 1);
      host.activate('motion');
      assert.ok(calls.indexOf('robot:deactivate') < calls.indexOf('motion:activate'));
      assert.equal(host.activeName, 'motion');
      assert.equal(scene.mode, 'pan');

      assert.throws(() => host.activate('broken'), /broken profile/);
      assert.equal(host.activeName, null);
      assert.equal(scene.onPointPick, null);
      assert.equal(typeof scene.onGoalCommit, 'function');

      host.activate('robot');
      host.dispose();
      assert.equal(host.activeName, null);
      assert.equal(host.profiles.size, 0);
      assert.equal(disposed, 2);
    """
    subprocess.run(
        [NODE, "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
