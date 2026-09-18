/* ==========================================================================
   PCM UI - comportamento compartilhado do Design System.
   Sem dependencias alem do Bootstrap ja carregado nas paginas.
   ========================================================================== */
(function (global) {
    'use strict';

    const reduzMovimento = global.matchMedia && global.matchMedia('(prefers-reduced-motion: reduce)').matches;

    /* ── Cores de status para gráficos ─────────────────────────────────────
       Lê os tokens do CSS em vez de repetir hex no código: assim claro e escuro
       seguem sozinhos, e a paleta validada (daltonismo + contraste) vale pros
       gráficos também, não só pros badges. */
    function token(nome, alternativo) {
        const v = getComputedStyle(document.documentElement).getPropertyValue(nome).trim();
        return v || alternativo;
    }
    const cores = {
        get realizada() { return token('--pcm-realizada', '#12744a'); },
        get pendente()  { return token('--pcm-pendente',  '#7d8791'); },
        get bloqueada() { return token('--pcm-bloqueada', '#c92a3a'); },
        get quebrada()  { return token('--pcm-quebrada',  '#b98900'); },
        get atrasada()  { return token('--pcm-atrasada',  '#cf6200'); },
        get marca()     { return token('--pcm-brand',     '#1E4B9A'); },
        get texto()     { return token('--pcm-text',      '#1f2937'); },
        get textoFraco(){ return token('--pcm-text-mute', '#7c8898'); },
        get superficie(){ return token('--pcm-surface',   '#ffffff'); }
    };

    /* Como está a aderência: devolve o sentimento, não só o número. Um só lugar
       define os limiares, então KPI, barra e gráfico contam a mesma história. */
    function sentimentoAderencia(perc) {
        if (perc >= 90) return { nivel: 'otimo',   cor: cores.realizada, icone: 'bi-check-circle-fill',        texto: 'No alvo' };
        if (perc >= 80) return { nivel: 'bom',     cor: cores.realizada, icone: 'bi-hand-thumbs-up-fill',      texto: 'Dentro da meta' };
        if (perc >= 50) return { nivel: 'atencao', cor: cores.quebrada,  icone: 'bi-exclamation-triangle-fill', texto: 'Requer atenção' };
        return                 { nivel: 'critico', cor: cores.bloqueada, icone: 'bi-exclamation-octagon-fill', texto: 'Crítico' };
    }

    /* Tendência entre o primeiro e o último ponto de uma série. */
    function tendencia(serie) {
        if (!serie || serie.length < 2) return { dir: 'estavel', delta: 0, icone: 'bi-dash', cor: cores.pendente, texto: 'sem variação' };
        const delta = Math.round(serie[serie.length - 1] - serie[0]);
        if (delta >= 3)  return { dir: 'subindo', delta, icone: 'bi-arrow-up-right',   cor: cores.realizada, texto: `subiu ${delta} p.p.` };
        if (delta <= -3) return { dir: 'caindo',  delta, icone: 'bi-arrow-down-right', cor: cores.bloqueada, texto: `caiu ${Math.abs(delta)} p.p.` };
        return { dir: 'estavel', delta, icone: 'bi-arrow-right', cor: cores.pendente, texto: 'estável' };
    }

    /* ── Sidebar (drawer no celular) ───────────────────────────────────── */
    function alternarSidebar(forcar) {
        const sb = document.getElementById('pcmSidebar');
        const bd = document.getElementById('pcmBackdrop');
        if (!sb) return;
        const abrir = (typeof forcar === 'boolean') ? forcar : !sb.classList.contains('open');
        sb.classList.toggle('open', abrir);
        if (bd) bd.classList.toggle('show', abrir);
        document.body.style.overflow = abrir && global.innerWidth < 992 ? 'hidden' : '';
    }

    /* ── Sidebar (esconder/mostrar no desktop) ───────────────────────────
       A classe fica na <html> (não num elemento do corpo) porque este
       trecho roda ainda no <head>, antes do <body> existir - assim a
       preferência já vale no primeiro paint, sem a barra "piscar" aberta
       pra depois sumir. */
    try {
        if (global.localStorage && localStorage.getItem('pcmSidebarEscondida') === '1') {
            document.documentElement.classList.add('pcm-sb-hidden');
        }
    } catch (e) { /* localStorage bloqueado (aba anonima, etc.) - segue sem lembrar */ }

    function alternarSidebarDesktop(forcar) {
        const html = document.documentElement;
        const esconder = (typeof forcar === 'boolean') ? forcar : !html.classList.contains('pcm-sb-hidden');
        html.classList.toggle('pcm-sb-hidden', esconder);
        try { global.localStorage && localStorage.setItem('pcmSidebarEscondida', esconder ? '1' : '0'); } catch (e) {}
    }

    /* ── Contador animado (0 → valor) ──────────────────────────────────── */
    function animarNumero(el, destino, opcoes) {
        opcoes = opcoes || {};
        const dur = opcoes.duracao || 900;
        const casas = opcoes.casas != null ? opcoes.casas : 0;
        const sufixo = opcoes.sufixo || '';
        const prefixo = opcoes.prefixo || '';
        const alvo = Number(destino) || 0;

        if (reduzMovimento) {
            el.textContent = prefixo + alvo.toFixed(casas).replace('.', ',') + sufixo;
            return;
        }
        const inicio = performance.now();
        function passo(agora) {
            const t = Math.min((agora - inicio) / dur, 1);
            const eased = 1 - Math.pow(1 - t, 3);
            const v = alvo * eased;
            el.textContent = prefixo + v.toFixed(casas).replace('.', ',') + sufixo;
            if (t < 1) requestAnimationFrame(passo);
        }
        requestAnimationFrame(passo);
    }

    /* Procura [data-contador] e anima quando entra na tela. */
    function iniciarContadores(raiz) {
        const alvos = (raiz || document).querySelectorAll('[data-contador]:not([data-contador-ok])');
        if (!alvos.length) return;

        const anima = el => {
            el.setAttribute('data-contador-ok', '1');
            animarNumero(el, el.getAttribute('data-contador'), {
                casas: parseInt(el.getAttribute('data-casas') || '0', 10),
                sufixo: el.getAttribute('data-sufixo') || '',
                prefixo: el.getAttribute('data-prefixo') || '',
                duracao: parseInt(el.getAttribute('data-duracao') || '900', 10)
            });
        };

        if (!('IntersectionObserver' in global)) { alvos.forEach(anima); return; }
        const obs = new IntersectionObserver(entradas => {
            entradas.forEach(e => {
                if (e.isIntersecting) { anima(e.target); obs.unobserve(e.target); }
            });
        }, { threshold: .25 });
        alvos.forEach(el => obs.observe(el));
    }

    /* ── Barras de progresso (.pcm-progress > span[data-valor]) ────────── */
    function iniciarBarras(raiz) {
        (raiz || document).querySelectorAll('.pcm-progress > span[data-valor]').forEach(b => {
            const v = Math.max(0, Math.min(100, parseFloat(b.getAttribute('data-valor')) || 0));
            requestAnimationFrame(() => { b.style.width = v + '%'; });
        });
    }

    /* ── Toast de feedback ─────────────────────────────────────────────── */
    const ICONES = { ok: 'bi-check-circle-fill', erro: 'bi-x-circle-fill', alerta: 'bi-exclamation-triangle-fill', info: 'bi-info-circle-fill' };
    function toast(mensagem, tipo, ms) {
        tipo = tipo || 'ok';
        let wrap = document.querySelector('.pcm-toast-wrap');
        if (!wrap) {
            wrap = document.createElement('div');
            wrap.className = 'pcm-toast-wrap';
            document.body.appendChild(wrap);
        }
        const el = document.createElement('div');
        el.className = 'pcm-toast pcm-toast--' + tipo;
        el.innerHTML = '<i class="bi ' + (ICONES[tipo] || ICONES.info) + '"></i><span></span>';
        el.querySelector('span').textContent = mensagem;
        wrap.appendChild(el);
        setTimeout(() => {
            el.classList.add('out');
            setTimeout(() => el.remove(), 300);
        }, ms || 2600);
        return el;
    }

    /* ── Tabela: ordenacao por coluna ──────────────────────────────────── */
    function valorCelula(tr, idx) {
        const td = tr.children[idx];
        if (!td) return '';
        const bruto = td.getAttribute('data-valor');
        return (bruto != null ? bruto : td.textContent).trim();
    }

    function ordenarTabela(tabela, idx, dir) {
        const corpo = tabela.tBodies[0];
        if (!corpo) return;
        const linhas = Array.from(corpo.rows);
        const sinal = dir === 'desc' ? -1 : 1;
        linhas.sort((a, b) => {
            const va = valorCelula(a, idx), vb = valorCelula(b, idx);
            const na = parseFloat(va.replace(/\./g, '').replace(',', '.'));
            const nb = parseFloat(vb.replace(/\./g, '').replace(',', '.'));
            const numerico = !isNaN(na) && !isNaN(nb) && /^[\d.,\-\s%R$]+$/.test(va) && /^[\d.,\-\s%R$]+$/.test(vb);
            if (numerico) return (na - nb) * sinal;
            return va.localeCompare(vb, 'pt-BR', { numeric: true, sensitivity: 'base' }) * sinal;
        });
        linhas.forEach(l => corpo.appendChild(l));
        tabela.classList.remove('pcm-fade-swap'); void tabela.offsetWidth;
        tabela.classList.add('pcm-fade-swap');
    }

    // Vale para as tabelas do design system e para as antigas que so querem o
    // comportamento de ordenacao (marcadas com data-pcm-sort), sem trocar o visual.
    function iniciarTabelas(raiz) {
        (raiz || document).querySelectorAll('table.pcm-table, table[data-pcm-sort]').forEach(tabela => {
            if (tabela.hasAttribute('data-pcm-ok')) return;
            tabela.setAttribute('data-pcm-ok', '1');
            Array.from(tabela.querySelectorAll('thead th[data-sort]')).forEach((th, i) => {
                const idx = th.cellIndex >= 0 ? th.cellIndex : i;
                th.addEventListener('click', () => {
                    const atual = th.getAttribute('data-dir');
                    const dir = atual === 'asc' ? 'desc' : 'asc';
                    tabela.querySelectorAll('thead th[data-sort]').forEach(o => o.removeAttribute('data-dir'));
                    th.setAttribute('data-dir', dir);
                    ordenarTabela(tabela, idx, dir);
                });
            });
        });
    }

    /* ── Busca dentro de uma tabela ────────────────────────────────────── */
    function filtrarTabela(tabela, termo) {
        const t = (termo || '').trim().toLowerCase();
        const corpo = tabela.tBodies[0];
        if (!corpo) return 0;
        let visiveis = 0;
        Array.from(corpo.rows).forEach(tr => {
            if (tr.hasAttribute('data-sem-filtro')) return;
            const bate = !t || tr.textContent.toLowerCase().includes(t);
            tr.style.display = bate ? '' : 'none';
            if (bate) visiveis++;
        });
        return visiveis;
    }

    /* ── debounce util pra campos de busca ─────────────────────────────── */
    function debounce(fn, ms) {
        let id;
        return function () {
            const ctx = this, args = arguments;
            clearTimeout(id);
            id = setTimeout(() => fn.apply(ctx, args), ms || 180);
        };
    }

    /* ── Lista suspensa animada ────────────────────────────────────────────
       O popup de um <select> nativo e desenhado pelo sistema operacional e nao
       aceita animacao. Entao o select continua ali (invisivel) cuidando do
       valor, do formulario e dos onchange que ja existem, e por cima dele
       desenhamos uma lista de verdade em HTML - essa sim abre animada.
       Em telas de toque o nativo e melhor, entao ali nao trocamos. */
    const TOQUE = global.matchMedia && global.matchMedia('(pointer: coarse)').matches;

    function montarLista(select) {
        if (select.dataset.pcmLista || select.multiple || select.size > 1) return;
        if (select.closest('.pcm-lista')) return;
        select.dataset.pcmLista = '1';

        const caixa = document.createElement('div');
        caixa.className = 'pcm-lista';
        select.parentNode.insertBefore(caixa, select);
        caixa.appendChild(select);

        const botao = document.createElement('button');
        botao.type = 'button';
        botao.className = 'pcm-lista-botao form-select';
        botao.setAttribute('aria-haspopup', 'listbox');
        botao.setAttribute('aria-expanded', 'false');
        caixa.appendChild(botao);

        const painel = document.createElement('div');
        painel.className = 'pcm-lista-painel';
        painel.setAttribute('role', 'listbox');
        caixa.appendChild(painel);

        const rotulo = () => {
            const o = select.options[select.selectedIndex];
            botao.textContent = o ? o.textContent.trim() : '';
        };

        function montarOpcoes() {
            painel.innerHTML = '';
            Array.from(select.options).forEach((op, i) => {
                const item = document.createElement('div');
                item.className = 'pcm-lista-item' + (i === select.selectedIndex ? ' ativo' : '');
                item.setAttribute('role', 'option');
                item.textContent = op.textContent.trim();
                if (op.disabled) item.classList.add('desabilitado');
                item.addEventListener('click', () => {
                    if (op.disabled) return;
                    select.selectedIndex = i;
                    rotulo();
                    // Dispara o change pra rodar exatamente o que ja rodava antes.
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                    fechar();
                });
                painel.appendChild(item);
            });
        }

        function abrir() {
            document.querySelectorAll('.pcm-lista.aberta').forEach(o => { if (o !== caixa) o.classList.remove('aberta'); });
            montarOpcoes();
            caixa.classList.add('aberta');
            botao.setAttribute('aria-expanded', 'true');
            const ativo = painel.querySelector('.ativo');
            if (ativo) ativo.scrollIntoView({ block: 'nearest' });
        }
        function fechar() {
            caixa.classList.remove('aberta');
            botao.setAttribute('aria-expanded', 'false');
        }

        botao.addEventListener('click', e => {
            e.preventDefault(); e.stopPropagation();
            caixa.classList.contains('aberta') ? fechar() : abrir();
        });
        botao.addEventListener('keydown', e => {
            if (e.key === 'Escape') fechar();
            if (e.key === 'Enter' || e.key === ' ' || e.key === 'ArrowDown') { e.preventDefault(); abrir(); }
        });
        // O change cobre a interacao do usuario. Quando o valor e trocado por
        // codigo (select.value = ...), nenhum evento dispara e o rotulo ficaria
        // mostrando a opcao antiga - por isso observamos o atributo tambem.
        select.addEventListener('change', rotulo);
        if (global.MutationObserver) {
            new MutationObserver(rotulo).observe(select, { attributes: true, childList: true, subtree: true });
        }
        // Rede curta pra trocas programaticas que nao mexem no DOM.
        setInterval(() => {
            const atual = select.options[select.selectedIndex];
            const esperado = atual ? atual.textContent.trim() : '';
            if (botao.textContent !== esperado) rotulo();
        }, 400);
        rotulo();
    }

    function iniciarListas(raiz) {
        if (TOQUE) return;
        (raiz || document).querySelectorAll('select.form-select:not([data-pcm-lista])').forEach(montarLista);
    }
    document.addEventListener('click', e => {
        if (!e.target.closest('.pcm-lista')) {
            document.querySelectorAll('.pcm-lista.aberta').forEach(c => {
                c.classList.remove('aberta');
                const b = c.querySelector('.pcm-lista-botao');
                if (b) b.setAttribute('aria-expanded', 'false');
            });
        }
    });

    /* ── Tooltips do Bootstrap em [data-bs-toggle="tooltip"] ───────────── */
    function iniciarTooltips(raiz) {
        if (!global.bootstrap || !global.bootstrap.Tooltip) return;
        (raiz || document).querySelectorAll('[data-bs-toggle="tooltip"]:not([data-tt-ok])').forEach(el => {
            el.setAttribute('data-tt-ok', '1');
            new global.bootstrap.Tooltip(el, { container: 'body' });
        });
    }

    /* ── Tema claro/escuro ─────────────────────────────────────────────── */
    function alternarTema() {
        const html = document.documentElement;
        const novo = html.getAttribute('data-bs-theme') === 'dark' ? 'light' : 'dark';
        html.setAttribute('data-bs-theme', novo);
        try { localStorage.setItem('pcm-tema', novo); } catch (e) { /* modo privado */ }
        document.querySelectorAll('[data-icone-tema]').forEach(i => {
            i.className = 'bi ' + (novo === 'dark' ? 'bi-sun-fill' : 'bi-moon-fill');
        });
    }
    function aplicarTemaSalvo() {
        let salvo = null;
        try { salvo = localStorage.getItem('pcm-tema'); } catch (e) { /* modo privado */ }
        if (salvo) {
            document.documentElement.setAttribute('data-bs-theme', salvo);
            document.querySelectorAll('[data-icone-tema]').forEach(i => {
                i.className = 'bi ' + (salvo === 'dark' ? 'bi-sun-fill' : 'bi-moon-fill');
            });
        }
    }

    /* ── Entrada animada (equivalente ao AnimatedContent) ──────────────────
       O sistema e Flask/Jinja, sem React - entao o mesmo comportamento vem do
       GSAP direto: o elemento comeca deslocado pra baixo, transparente e um
       pouco menor, e assume a posicao quando entra na tela. */
    const ANIM_PADRAO = {
        distancia: 80, duracao: .8, ease: 'power3.out',
        escala: .97, limiar: .1, atraso: .05
    };

    function animarEntrada(alvo, opcoes) {
        if (!global.gsap || reduzMovimento) return;
        const o = Object.assign({}, ANIM_PADRAO, opcoes || {});
        const elementos = typeof alvo === 'string' ? document.querySelectorAll(alvo) : alvo;
        if (!elementos || !elementos.length) return;

        gsap.set(elementos, { y: o.distancia, opacity: 0, scale: o.escala });
        gsap.to(elementos, {
            y: 0, opacity: 1, scale: 1,
            duration: o.duracao, ease: o.ease, delay: o.atraso,
            // Cascata curta: cada item entra logo depois do anterior.
            stagger: o.cascata != null ? o.cascata : .05,
            scrollTrigger: global.ScrollTrigger ? {
                trigger: elementos[0],
                start: `top ${100 - o.limiar * 100}%`,
                once: true
            } : undefined
        });

        // Rede de seguranca: animacao de entrada parte de opacity 0, entao se o
        // gatilho nao disparar (aba em segundo plano, layout que muda, erro no
        // ScrollTrigger) o conteudo ficaria invisivel. Passado o tempo da
        // animacao, o que ainda estiver apagado e revelado na marra.
        setTimeout(() => {
            Array.from(elementos).forEach(el => {
                if (parseFloat(getComputedStyle(el).opacity) < .9) {
                    gsap.set(el, { y: 0, opacity: 1, scale: 1, clearProps: 'transform' });
                }
            });
        }, (o.duracao + o.atraso) * 1000 + 2200);
    }

    /* Entrada padrao das telas: blocos do conteudo sobem em sequencia. */
    function iniciarEntradas(raiz) {
        if (!global.gsap || reduzMovimento) return;
        if (global.ScrollTrigger) gsap.registerPlugin(global.ScrollTrigger);

        const principal = (raiz || document).querySelector('.pcm-main');
        if (!principal || principal.dataset.pcmEntrada) return;
        principal.dataset.pcmEntrada = '1';

        // Filhos diretos entram de baixo pra cima, um apos o outro.
        const blocos = Array.from(principal.children).filter(el => el.offsetParent !== null);
        if (blocos.length) animarEntrada(blocos, { cascata: .06 });

        // Cards de indicador tem cascata propria, mais curta (0, .05, .10, .15).
        principal.querySelectorAll('.pcm-kpi-grid, .stat-grid, .ferr-stat-grid').forEach(grade => {
            const cards = grade.children;
            if (!cards.length) return;
            gsap.set(cards, { y: 26, opacity: 0, scale: .97 });
            gsap.to(cards, {
                y: 0, opacity: 1, scale: 1, duration: .55, ease: 'power3.out',
                stagger: .05,
                scrollTrigger: global.ScrollTrigger ? { trigger: grade, start: 'top 92%', once: true } : undefined
            });
            setTimeout(() => {
                Array.from(cards).forEach(c => {
                    if (parseFloat(getComputedStyle(c).opacity) < .9) {
                        gsap.set(c, { y: 0, opacity: 1, scale: 1 });
                    }
                });
            }, 2800);
        });
    }

    /* Troca de aba: o conteudo novo sobe com fade em vez de aparecer seco. */
    function iniciarTransicaoAbas(raiz) {
        if (!global.gsap || reduzMovimento) return;
        (raiz || document).querySelectorAll('[data-bs-toggle="tab"], [data-bs-toggle="pill"]').forEach(gatilho => {
            if (gatilho.dataset.pcmAba) return;
            gatilho.dataset.pcmAba = '1';
            gatilho.addEventListener('shown.bs.tab', e => {
                const alvo = document.querySelector(e.target.getAttribute('data-bs-target'));
                if (!alvo) return;
                gsap.fromTo(alvo,
                    { y: 22, opacity: 0 },
                    { y: 0, opacity: 1, duration: .45, ease: 'power3.out' });
                if (global.ScrollTrigger) ScrollTrigger.refresh();
            });
        });
    }

    function iniciarTudo(raiz) {
        iniciarContadores(raiz);
        iniciarBarras(raiz);
        iniciarTabelas(raiz);
        iniciarTooltips(raiz);
        iniciarListas(raiz);
        iniciarEntradas(raiz);
        iniciarTransicaoAbas(raiz);
        iniciarKpiSparklines(raiz);
    }

    /* ── Loading global ────────────────────────────────────────────────────
       Uma unica tela de carregamento para o sistema inteiro, com mensagem
       contextual. show()/hide() contam chamadas (pode ter duas operacoes
       simultaneas) para nao esconder o loading de uma enquanto a outra ainda
       roda. Chame loading.show('Gerando relatório...') antes de um fetch
       demorado e loading.hide() no finally. */
    let _loadingAtivos = 0, _loadingEl = null;
    function _montarLoading() {
        if (_loadingEl) return _loadingEl;
        const el = document.createElement('div');
        el.className = 'pcm-loading-overlay';
        el.innerHTML = '<div class="pcm-loading-caixa">' +
            '<div class="pcm-loading-spin" aria-hidden="true"></div>' +
            '<div class="pcm-loading-msg">Carregando…</div></div>';
        document.body.appendChild(el);
        _loadingEl = el;
        return el;
    }
    function loadingShow(mensagem) {
        const el = _montarLoading();
        el.querySelector('.pcm-loading-msg').textContent = mensagem || 'Carregando…';
        _loadingAtivos++;
        el.classList.add('show');
    }
    function loadingHide() {
        _loadingAtivos = Math.max(0, _loadingAtivos - 1);
        if (_loadingAtivos === 0 && _loadingEl) _loadingEl.classList.remove('show');
    }
    // Envolve um fetch (ou qualquer Promise) com o loading, garantindo hide()
    // mesmo se a requisicao falhar - o padrao usado em toda ação demorada.
    function loadingEnvolver(promessa, mensagem) {
        loadingShow(mensagem);
        return Promise.resolve(promessa).finally(loadingHide);
    }

    /* ── Drill-down genérico ───────────────────────────────────────────────
       Um card ou fatia de gráfico chama PCM.drilldown(url, titulo) e recebe
       um modal com a lista de frotas/serviços que compõem aquele número -
       sem cada tela precisar montar o próprio modal. O endpoint deve devolver
       JSON: { titulo, subtitulo, colunas: ['Frota','Situação',...], linhas: [[...]] }
       ou, mais simples, { itens: [{frota, descricao, situacao, cor}] }. */
    let _drillModalEl = null, _drillModalUI = null;
    function _montarDrillModal() {
        if (_drillModalEl) return;
        const wrap = document.createElement('div');
        wrap.innerHTML = `<div class="modal fade" id="pcmDrillModal" tabindex="-1">
            <div class="modal-dialog modal-dialog-scrollable modal-lg">
                <div class="modal-content">
                    <div class="modal-header">
                        <div>
                            <h6 class="modal-title fw-bold mb-0" id="pcmDrillTitulo"></h6>
                            <small class="text-muted" id="pcmDrillSubtitulo"></small>
                        </div>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body p-0">
                        <div id="pcmDrillCorpo" class="pcm-drill-corpo"></div>
                    </div>
                </div>
            </div>
        </div>`;
        document.body.appendChild(wrap.firstElementChild);
        _drillModalEl = document.getElementById('pcmDrillModal');
        if (global.bootstrap) _drillModalUI = new global.bootstrap.Modal(_drillModalEl);
    }
    function _corStatus(situacao) {
        const s = (situacao || '').toUpperCase();
        if (s.includes('REALIZ')) return 'realizada';
        if (s.includes('BLOQ'))   return 'bloqueada';
        if (s.includes('QUEB'))   return 'quebrada';
        if (s.includes('ATRAS')) return 'atrasada';
        return 'pendente';
    }
    function _renderDrillLinhas(itens) {
        if (!itens || !itens.length) {
            return '<div class="pcm-drill-vazio"><i class="bi bi-inbox"></i><span>Nada encontrado para este filtro.</span></div>';
        }
        return '<ul class="pcm-drill-lista">' + itens.map(it => {
            const cor = it.cor || _corStatus(it.situacao);
            return `<li class="pcm-drill-item">
                <span class="pcm-drill-dot pcm-drill-dot--${cor}"></span>
                <div class="pcm-drill-txt">
                    <strong>${it.frota != null ? it.frota : ''}</strong>
                    <span>${it.descricao || ''}</span>
                </div>
                <span class="pcm-status pcm-status--${cor}">${it.situacao || ''}</span>
            </li>`;
        }).join('') + '</ul>';
    }
    function drilldown(url, tituloFallback) {
        _montarDrillModal();
        document.getElementById('pcmDrillTitulo').textContent = tituloFallback || 'Detalhes';
        document.getElementById('pcmDrillSubtitulo').textContent = '';
        document.getElementById('pcmDrillCorpo').innerHTML =
            '<div class="pcm-drill-vazio"><div class="pcm-loading-spin pcm-loading-spin--sm"></div><span>Carregando…</span></div>';
        if (_drillModalUI) _drillModalUI.show();

        fetch(url).then(r => r.json()).then(dados => {
            document.getElementById('pcmDrillTitulo').textContent = dados.titulo || tituloFallback || 'Detalhes';
            document.getElementById('pcmDrillSubtitulo').textContent = dados.subtitulo || '';
            document.getElementById('pcmDrillCorpo').innerHTML = _renderDrillLinhas(dados.itens);
        }).catch(() => {
            document.getElementById('pcmDrillCorpo').innerHTML =
                '<div class="pcm-drill-vazio"><i class="bi bi-exclamation-triangle-fill"></i><span>Não deu para carregar os detalhes agora.</span></div>';
        });
    }
    // Mesmo modal, mas para quando os itens já estão em memória no browser
    // (ex.: um gráfico montado a partir de um array já carregado na página) -
    // sem essa variante, cada gráfico assim precisaria de um endpoint só pra
    // devolver de volta o que o próprio JS da página já tem.
    function drilldownDireto(itens, titulo, subtitulo) {
        _montarDrillModal();
        document.getElementById('pcmDrillTitulo').textContent = titulo || 'Detalhes';
        document.getElementById('pcmDrillSubtitulo').textContent = subtitulo || '';
        document.getElementById('pcmDrillCorpo').innerHTML = _renderDrillLinhas(itens);
        if (_drillModalUI) _drillModalUI.show();
    }

    /* ── KPI com tendência (sparkline de fundo) ────────────────────────────
       <div class="pcm-kpi pcm-kpi--flow" data-sparkline='{"labels":[...],
       "valores":[...],"sufixo":"%"}'> ... </div> - iniciarKpiSparklines monta
       o canvas de fundo e troca o valor exibido ao passar o mouse. */
    function _corTendencia(delta) {
        if (Math.abs(delta) < 0.05) return { classe: 'igual', icone: 'bi-dash', texto: 'estável' };
        return delta > 0
            ? { classe: 'alta',  icone: 'bi-arrow-up-right',   texto: '+' + delta.toFixed(1) }
            : { classe: 'baixa', icone: 'bi-arrow-down-right', texto: delta.toFixed(1) };
    }
    function iniciarKpiSparklines(raiz) {
        if (!global.Chart) return;
        (raiz || document).querySelectorAll('[data-sparkline]').forEach(card => {
            if (card.dataset.sparklineOk) return;
            let cfg;
            try { cfg = JSON.parse(card.getAttribute('data-sparkline')); } catch (e) { return; }
            const valores = (cfg.valores || []).map(Number).filter(v => !Number.isNaN(v));
            if (valores.length < 2) return;
            card.dataset.sparklineOk = '1';

            const valorEl  = card.querySelector('.pcm-kpi-value');
            const labelEl  = card.querySelector('.pcm-kpi-label');
            const tendEl   = card.querySelector('.pcm-kpi-tendencia');
            const sufixo   = cfg.sufixo || '';
            const casas    = cfg.casas != null ? cfg.casas : (sufixo === '%' ? 1 : 0);
            const rotuloBase = labelEl ? labelEl.textContent.trim() : '';
            const fmt = v => v.toFixed(casas).replace('.', ',') + sufixo;
            // Le do proprio [data-contador] em vez do textContent: o card ainda
            // pode estar no meio da animacao de contagem quando isto roda.
            const valorOriginal = () => valorEl
                ? (parseFloat(valorEl.getAttribute('data-contador')) || 0).toFixed(casas).replace('.', ',') + sufixo
                : '';

            const canvas = document.createElement('canvas');
            canvas.className = 'pcm-kpi-spark';
            card.appendChild(canvas);

            const corLinha = getComputedStyle(card).getPropertyValue('--pcm-kpi-color').trim() || cores.marca;
            const chart = new Chart(canvas, {
                type: 'line',
                data: {
                    labels: cfg.labels || valores.map((_, i) => i),
                    datasets: [{
                        data: valores, borderColor: corLinha, borderWidth: 2, fill: true,
                        backgroundColor: corLinha + '22', tension: .35, pointRadius: 0,
                        pointHoverRadius: 4, pointHoverBackgroundColor: corLinha, pointHitRadius: 18
                    }]
                },
                options: {
                    responsive: true, maintainAspectRatio: false,
                    animation: reduzMovimento ? false : { duration: 650, easing: 'easeOutQuart' },
                    interaction: { mode: 'index', intersect: false },
                    scales: { x: { display: false }, y: { display: false } },
                    plugins: { legend: { display: false }, tooltip: { enabled: false }, datalabels: { display: false } },
                    onHover(ev, els) {
                        if (!valorEl) return;
                        if (els && els.length) {
                            const i = els[0].index;
                            valorEl.textContent = fmt(valores[i]);
                            if (labelEl && cfg.labels) labelEl.textContent = cfg.labels[i];
                        } else {
                            valorEl.textContent = valorOriginal();
                            if (labelEl) labelEl.textContent = rotuloBase;
                        }
                    }
                }
            });
            card.addEventListener('mouseleave', () => {
                if (valorEl) valorEl.textContent = valorOriginal();
                if (labelEl) labelEl.textContent = rotuloBase;
            });

            if (tendEl) {
                const t = _corTendencia(valores[valores.length - 1] - valores[0]);
                tendEl.className = 'pcm-kpi-tendencia pcm-kpi-tendencia--' + t.classe;
                tendEl.innerHTML = `<i class="bi ${t.icone}"></i>${t.texto}${sufixo}`;
            }
        });
    }

    global.PCM = {
        alternarSidebar, alternarSidebarDesktop, animarNumero, iniciarContadores, iniciarBarras,
        toast, ordenarTabela, iniciarTabelas, filtrarTabela, debounce,
        iniciarTooltips, alternarTema, iniciarTudo, iniciarListas,
        animarEntrada, iniciarEntradas, iniciarTransicaoAbas,
        cores, sentimentoAderencia, tendencia,
        loading: { show: loadingShow, hide: loadingHide, envolver: loadingEnvolver },
        drilldown, drilldownDireto, iniciarKpiSparklines
    };

    // Com GSAP na pagina, ele cuida das entradas - a marca na raiz desliga as
    // animacoes equivalentes do CSS pra que os dois nao animem o mesmo elemento.
    if (global.gsap && !reduzMovimento) {
        document.documentElement.classList.add('pcm-gsap');
    }

    /* ── Animação padrão de TODOS os gráficos ──────────────────────────────
       Definido no Chart.defaults: vale para qualquer gráfico do sistema, novo
       ou antigo, sem precisar repetir configuração em cada arquivo. Barras
       crescem da base, linhas são traçadas da esquerda, anéis giram ao surgir. */
    function configurarAnimacaoGraficos() {
        if (!global.Chart || global.Chart.__pcmAnim) return;
        global.Chart.__pcmAnim = true;

        if (reduzMovimento) {
            Chart.defaults.animation = false;
            Chart.defaults.animations = {};
            return;
        }

        Chart.defaults.animation = { duration: 850, easing: 'easeOutQuart' };
        Chart.defaults.animation.delay = ctx =>
            ctx.type === 'data' && ctx.mode === 'default' ? ctx.dataIndex * 28 : 0;

        // Barra: sobe da linha de base em vez de aparecer pronta.
        Chart.defaults.datasets.bar.animations = {
            y: { from: ctx => ctx.chart.scales.y ? ctx.chart.scales.y.getPixelForValue(0) : undefined },
            x: { from: ctx => ctx.chart.scales.x ? ctx.chart.scales.x.getPixelForValue(0) : undefined }
        };
        // Anel: cresce do centro girando.
        Chart.defaults.datasets.doughnut.animation = { animateRotate: true, animateScale: true, duration: 900 };
        Chart.defaults.datasets.pie.animation      = { animateRotate: true, animateScale: true, duration: 900 };
        // Linha: o traço é desenhado da esquerda pra direita.
        Chart.defaults.datasets.line.animations = {
            x: { type: 'number', easing: 'linear', duration: 22, from: NaN,
                 delay: ctx => (ctx.type === 'data' && !ctx.dropped ? (ctx.dropped = true, ctx.dataIndex * 22) : undefined) },
            y: { type: 'number', easing: 'easeOutQuart', duration: 650,
                 from: ctx => ctx.chart.scales.y ? ctx.chart.scales.y.getPixelForValue(ctx.chart.scales.y.min) : undefined }
        };
        // Hover mais vivo, sem atrapalhar a leitura.
        Chart.defaults.hover = Object.assign({}, Chart.defaults.hover, { animationDuration: 180 });
    }

    // Chart.js pode carregar depois deste arquivo: tenta agora e no DOM pronto.
    configurarAnimacaoGraficos();
    document.addEventListener('DOMContentLoaded', configurarAnimacaoGraficos);

    aplicarTemaSalvo();
    document.addEventListener('DOMContentLoaded', () => {
        iniciarTudo(document);
        // Fecha o menu ao voltar pro desktop, senao o drawer fica "preso" aberto.
        global.addEventListener('resize', debounce(() => {
            if (global.innerWidth >= 992) alternarSidebar(false);
        }, 150));
    });
})(window);
