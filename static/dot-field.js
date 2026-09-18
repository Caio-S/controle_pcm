/* ── Dot Field (portado de React Bits para JS vanilla / canvas) ─────────────────
   Grade de pontos que "incha" e brilha perto do cursor. Uso:
       DotField.mount(containerEl, { dotRadius: 1.5, dotSpacing: 14, ... });
   Cria um <canvas> preenchendo o container (position:relative no container). */
(function () {
    function hexToRgb(hex) {
        hex = String(hex).replace('#', '');
        if (hex.length === 3) hex = hex.split('').map(c => c + c).join('');
        const n = parseInt(hex, 16);
        return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
    }
    function lerp(a, b, t) { return a + (b - a) * t; }
    function lerpColor(c1, c2, t) {
        return [lerp(c1[0], c2[0], t), lerp(c1[1], c2[1], t), lerp(c1[2], c2[2], t)];
    }

    function mount(container, opts) {
        opts = Object.assign({
            dotRadius: 1.5,
            dotSpacing: 14,
            bulgeStrength: 67,   // quanto o ponto cresce perto do cursor (%)
            glowRadius: 160,     // ate onde o efeito alcança, em px
            sparkle: false,      // pontos piscando aleatoriamente
            waveAmplitude: 0,    // ondulacao continua (px), 0 = desligada
            gradientFrom: '#3B82F6',
            gradientTo: '#3B82F6',
            glowColor: '#3B82F6',
            baseOpacity: 0.35,
        }, opts || {});

        const canvas = document.createElement('canvas');
        canvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block;pointer-events:none;';
        // So forca position:relative se o container ainda estiver 'static' (ex.: div sem
        // CSS proprio) - nunca sobrescreve um position:fixed/absolute ja definido no CSS.
        if (getComputedStyle(container).position === 'static') container.style.position = 'relative';
        container.insertBefore(canvas, container.firstChild);
        const ctx = canvas.getContext('2d');

        const colFrom = hexToRgb(opts.gradientFrom);
        const colTo = hexToRgb(opts.gradientTo);
        const glowRgb = hexToRgb(opts.glowColor);

        let W = 0, H = 0, dpr = Math.min(window.devicePixelRatio || 1, 2);
        let dots = [];
        let mouseX = -9999, mouseY = -9999;
        let targetMouseX = -9999, targetMouseY = -9999;
        let raf = null, t0 = performance.now();

        function resize() {
            const rect = container.getBoundingClientRect();
            W = rect.width; H = rect.height;
            canvas.width = W * dpr; canvas.height = H * dpr;
            ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
            buildGrid();
        }

        function buildGrid() {
            dots = [];
            const spacing = opts.dotSpacing;
            const cols = Math.ceil(W / spacing) + 1;
            const rows = Math.ceil(H / spacing) + 1;
            for (let r = 0; r < rows; r++) {
                for (let c = 0; c < cols; c++) {
                    dots.push({
                        x: c * spacing, y: r * spacing,
                        r: 0, phase: Math.random() * Math.PI * 2,
                        sparkleSeed: Math.random()
                    });
                }
            }
        }

        function onMove(e) {
            const rect = container.getBoundingClientRect();
            targetMouseX = e.clientX - rect.left;
            targetMouseY = e.clientY - rect.top;
        }
        function onLeave() { targetMouseX = -9999; targetMouseY = -9999; }

        function draw(now) {
            const t = (now - t0) / 1000;
            mouseX = lerp(mouseX, targetMouseX, 0.18);
            mouseY = lerp(mouseY, targetMouseY, 0.18);

            ctx.clearRect(0, 0, W, H);
            const tx = W > 0 ? 1 / W : 0;

            for (let i = 0; i < dots.length; i++) {
                const d = dots[i];
                let x = d.x, y = d.y;
                if (opts.waveAmplitude > 0) {
                    y += Math.sin(t * 1.2 + d.phase) * opts.waveAmplitude;
                }
                const dx = x - mouseX, dy = y - mouseY;
                const dist = Math.sqrt(dx * dx + dy * dy);
                let bulge = 0, glow = 0;
                if (dist < opts.glowRadius) {
                    const p = 1 - dist / opts.glowRadius;
                    bulge = p * p * (opts.bulgeStrength / 100);
                    glow = p * p;
                }
                let sparkle = 1;
                if (opts.sparkle) sparkle = 0.6 + 0.4 * Math.sin(t * 3 + d.sparkleSeed * 10);

                const radius = opts.dotRadius * (1 + bulge) * sparkle;
                const colorT = W > 0 ? x * tx : 0;
                const [cr, cg, cb] = lerpColor(colFrom, colTo, colorT);
                const [gr, gg, gb] = glowRgb;
                const rr = lerp(cr, gr, glow), gg2 = lerp(cg, gg, glow), bb = lerp(cb, gb, glow);
                const alpha = Math.min(opts.baseOpacity + glow * (1 - opts.baseOpacity), 1);

                ctx.beginPath();
                ctx.fillStyle = `rgba(${rr | 0}, ${gg2 | 0}, ${bb | 0}, ${alpha.toFixed(3)})`;
                ctx.arc(x, y, Math.max(radius, 0.1), 0, Math.PI * 2);
                ctx.fill();
            }
            raf = requestAnimationFrame(draw);
        }

        const ro = new ResizeObserver(resize);
        ro.observe(container);
        container.addEventListener('pointermove', onMove);
        container.addEventListener('pointerleave', onLeave);
        resize();
        raf = requestAnimationFrame(draw);

        return {
            destroy() {
                cancelAnimationFrame(raf);
                ro.disconnect();
                container.removeEventListener('pointermove', onMove);
                container.removeEventListener('pointerleave', onLeave);
                canvas.remove();
            }
        };
    }

    window.DotField = { mount };
})();
