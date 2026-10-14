/* Simplified 3D pitch. Not a physically accurate trajectory. */
(function (global) {
  function outcomeOf(ball) {
    const extras = ball.extras || {};
    if (extras.wides) return "wide";
    if (extras.noballs) return "noball";
    if (ball.wickets && ball.wickets.length) return "wicket";
    if (ball.runs_batter === 4 || ball.runs_batter === 6) return "boundary";
    if ((ball.runs_total || 0) === 0) return "dot";
    return "single";
  }

  const COLORS = {
    dot: 0x2A354E,
    single: 0x00E5FF,
    boundary: 0xFFC107,
    wicket: 0xFF2A55,
    wide: 0xFF5E00,
    noball: 0xCC00FF,
  };

  function createPitch(container) {
    if (!global.THREE || !global.WebGLRenderingContext) {
      container.innerHTML =
        '<div class="webgl-fallback">WebGL is unavailable. Ball-by-ball replay continues in the 2D feed. The 3D view is a simplified visualization, not a physical trajectory.</div>';
      return null;
    }
    let renderer;
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    } catch (err) {
      container.innerHTML =
        '<div class="webgl-fallback">WebGL could not start. Using the 2D replay feed instead.</div>';
      return null;
    }
    const scene = new THREE.Scene();
    const camera = new THREE.PerspectiveCamera(42, 1, 0.1, 100);
    camera.position.set(0, 7.2, 11.5);
    camera.lookAt(0, 0, 0);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    container.innerHTML = "";
    const canvas = renderer.domElement;
    canvas.id = "pitch-canvas";
    container.appendChild(canvas);

    const ground = new THREE.Mesh(
      new THREE.PlaneGeometry(16, 22),
      new THREE.MeshLambertMaterial({ color: 0x14532d })
    );
    ground.rotation.x = -Math.PI / 2;
    scene.add(ground);

    const strip = new THREE.Mesh(
      new THREE.BoxGeometry(2.2, 0.04, 14),
      new THREE.MeshLambertMaterial({ color: 0xc4b59a })
    );
    strip.position.y = 0.03;
    scene.add(strip);

    function stumps(z) {
      const group = new THREE.Group();
      for (let i = -1; i <= 1; i += 1) {
        const s = new THREE.Mesh(
          new THREE.CylinderGeometry(0.04, 0.04, 0.7, 8),
          new THREE.MeshLambertMaterial({ color: 0xf4efe4 })
        );
        s.position.set(i * 0.16, 0.35, z);
        group.add(s);
      }
      scene.add(group);
    }
    stumps(-6.4);
    stumps(6.4);

    const bowler = new THREE.Mesh(
      new THREE.SphereGeometry(0.22, 12, 12),
      new THREE.MeshLambertMaterial({ color: 0x7aa2ff })
    );
    bowler.position.set(0.35, 0.4, -6.8);
    scene.add(bowler);
    const batter = new THREE.Mesh(
      new THREE.SphereGeometry(0.22, 12, 12),
      new THREE.MeshLambertMaterial({ color: 0xe2b857 })
    );
    batter.position.set(0, 0.4, 6.5);
    scene.add(batter);

    const ball = new THREE.Mesh(
      new THREE.SphereGeometry(0.18, 16, 16),
      new THREE.MeshPhysicalMaterial({ color: 0xFF5E00, emissive: 0xFF5E00, emissiveIntensity: 0.8, clearcoat: 1 })
    );
    ball.position.set(0.35, 0.5, -6.2);
    scene.add(ball);
    scene.add(new THREE.AmbientLight(0x00E5FF, 0.4));
    const sun = new THREE.DirectionalLight(0xFF5E00, 1.2);
    sun.position.set(4, 10, 6);
    scene.add(sun);

    let raf = 0;
    function resize() {
      const w = container.clientWidth || 480;
      const h = 340;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
    }
    resize();
    window.addEventListener("resize", resize);

    function loop() {
      raf = requestAnimationFrame(loop);
      renderer.render(scene, camera);
    }
    loop();

    return {
      playBall(delivery) {
        const kind = outcomeOf(delivery);
        const colorHex = COLORS[kind] || COLORS.single; ball.material.color.setHex(colorHex); ball.material.emissive.setHex(colorHex);
        const start = { x: 0.35, y: 0.5, z: -6.2 };
        const endZ = kind === "wide" ? 7.4 : 6.3;
        const endX = kind === "wide" ? 1.1 : kind === "boundary" ? 2.2 : 0.05;
        const peak = kind === "dot" ? 0.7 : 1.6;
        const t0 = performance.now();
        const dur = 650;
        function step(now) {
          const t = Math.min(1, (now - t0) / dur);
          const ease = t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
          ball.position.x = start.x + (endX - start.x) * ease;
          ball.position.z = start.z + (endZ - start.z) * ease;
          ball.position.y = start.y + Math.sin(Math.PI * t) * peak;
          camera.position.x = Math.sin(t * 0.4) * 0.35;
          if (t < 1) requestAnimationFrame(step);
        }
        requestAnimationFrame(step);
      },
      dispose() {
        cancelAnimationFrame(raf);
        renderer.dispose();
      },
    };
  }

  global.Pitch3D = { createPitch, outcomeOf };
})(window);
