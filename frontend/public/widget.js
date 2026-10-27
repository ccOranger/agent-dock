/*
 * AgentDock 嵌入组件（一行 script 引入）
 *
 *   <script src="https://your-host/widget.js" data-scene="mall_order"
 *           data-ext='{"platform":"web","channel":"miniapp"}'></script>
 *
 * data-integration 是接入方公开标识：只用于匿名访客归属与命名空间，
 * 不是密钥；不填时访客落在场景级匿名池。
 * data-ext 是客户端声明的埋点上下文（渠道等设备信息都作为其中的普通键），
 * 只用于统计，不是身份或租户凭证。
 *
 * 组件会读取场景公开配置（标题/主题色/位置），创建右下角气泡，点击后加载
 * /embed.html 到 iframe。iframe 与宿主跨域隔离，宿主 CSS 不会影响对话界面。
 */
(function () {
  var script = document.currentScript;
  if (!script) return;

  var scene = script.getAttribute('data-scene');
  if (!scene) {
    console.warn('[AgentDock] 缺少 data-scene，嵌入代码未生效');
    return;
  }
  var integration = (script.getAttribute('data-integration') || '').trim();
  var ext = (script.getAttribute('data-ext') || '').trim();
  var scriptUrl = new URL(script.src);
  var origin = scriptUrl.origin;
  var apiBase = (script.getAttribute('data-api') || origin).replace(/\/$/, '');
  var zIndex = script.getAttribute('data-z') || '2147483000';

  var state = {
    open: false,
    unread: 0,
    iframe: null,
    panel: null,
    button: null,
    dot: null,
    title: script.getAttribute('data-title') || '在线客服',
    position: script.getAttribute('data-position') || 'right',
    color: script.getAttribute('data-theme') || '#2b5aa0',
  };

  function el(tag, styles, html) {
    var node = document.createElement(tag);
    for (var key in styles) {
      if (Object.prototype.hasOwnProperty.call(styles, key)) node.style[key] = styles[key];
    }
    if (html) node.innerHTML = html;
    return node;
  }

  // 埋点 ext 随 iframe 传给嵌入页，最终进入聊天请求的 ext 字段
  function extraQuery() {
    return ext ? '&ext=' + encodeURIComponent(ext) : '';
  }

  var BUBBLE_ICON =
    '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M21 11.5a8.4 8.4 0 0 1-9 8.4 8.9 8.9 0 0 1-3.8-.9L3 21l1.9-5.1' +
    'a8.4 8.4 0 0 1-.9-3.9 8.5 8.5 0 0 1 8.5-8.5h.5a8.5 8.5 0 0 1 8 8.5z"/></svg>';

  function isMobile() {
    return window.innerWidth <= 640;
  }

  function applyPosition() {
    var side = state.position === 'left' ? { left: '20px' } : { right: '20px' };
    var panelSide = state.position === 'left' ? { left: '20px' } : { right: '20px' };
    var bottom = '84px';
    if (isMobile()) {
      // 选左边还是右边由后台配置决定；面板在移动端全屏，左右贴边无意义
      side = state.position === 'left' ? { left: '16px' } : { right: '16px' };
      panelSide = { left: '0px' };
      bottom = '0px';
    }
    // 先清空对侧：只改一侧会留下旧的 left/right，宽度固定时后设的那侧不生效
    Object.assign(state.button.style, { left: '', right: '' }, side, { bottom: '20px' });
    Object.assign(state.panel.style, { left: '', right: '' }, panelSide, { bottom: bottom });
    if (isMobile()) {
      state.panel.style.width = '100%';
      state.panel.style.height = '100%';
      state.panel.style.right = '0';
      state.panel.style.left = '0';
      state.panel.style.bottom = '0';
      state.panel.style.borderRadius = '0';
    } else {
      state.panel.style.width = '380px';
      state.panel.style.height = 'min(620px, calc(100vh - 110px))';
      state.panel.style.borderRadius = '14px';
    }
  }

  function ensureIframe() {
    if (state.iframe) return state.iframe;
    var iframe = document.createElement('iframe');
    iframe.src =
      origin +
      '/embed.html?scene=' +
      encodeURIComponent(scene) +
      (integration ? '&integration=' + encodeURIComponent(integration) : '') +
      extraQuery();
    iframe.title = state.title;
    iframe.setAttribute('allow', 'clipboard-write');
    iframe.style.width = '100%';
    iframe.style.height = '100%';
    iframe.style.border = '0';
    iframe.style.display = 'block';
    iframe.style.background = '#fff';
    state.panel.appendChild(iframe);
    state.iframe = iframe;
    return iframe;
  }

  function paintDot() {
    state.dot.style.display = state.unread > 0 && !state.open ? 'block' : 'none';
    state.dot.textContent = state.unread > 9 ? '9+' : String(state.unread);
  }

  function open() {
    ensureIframe();
    state.open = true;
    state.panel.style.display = 'flex';
    // 触发一次 reflow，保证 transition 生效
    void state.panel.offsetHeight;
    state.panel.style.opacity = '1';
    state.panel.style.transform = 'translateY(0)';
    state.unread = 0;
    paintDot();
    state.button.setAttribute('aria-expanded', 'true');
  }

  function close() {
    state.open = false;
    state.panel.style.opacity = '0';
    state.panel.style.transform = 'translateY(12px)';
    setTimeout(function () {
      if (!state.open) state.panel.style.display = 'none';
    }, 180);
    state.button.setAttribute('aria-expanded', 'false');
  }

  function toggle() {
    if (state.open) close();
    else open();
  }

  function build() {
    var baseButton = {
      position: 'fixed',
      right: '20px',
      bottom: '20px',
      width: '56px',
      height: '56px',
      borderRadius: '50%',
      border: '0',
      background: state.color,
      color: '#fff',
      boxShadow: '0 6px 20px rgba(0,0,0,.22)',
      cursor: 'pointer',
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'center',
      zIndex: zIndex,
      padding: '0',
      transition: 'transform .15s ease',
    };
    var button = el('button', baseButton, BUBBLE_ICON);
    button.type = 'button';
    button.setAttribute('aria-label', '打开在线客服');
    button.setAttribute('aria-expanded', 'false');
    button.onmouseenter = function () {
      button.style.transform = 'scale(1.05)';
    };
    button.onmouseleave = function () {
      button.style.transform = 'scale(1)';
    };
    button.onclick = toggle;

    var dot = el('span', {
      position: 'absolute',
      top: '-2px',
      right: '-2px',
      minWidth: '18px',
      height: '18px',
      borderRadius: '9px',
      background: '#f56c6c',
      color: '#fff',
      fontSize: '11px',
      lineHeight: '18px',
      textAlign: 'center',
      padding: '0 4px',
      display: 'none',
      boxSizing: 'border-box',
    });
    button.appendChild(dot);

    var panel = el('div', {
      position: 'fixed',
      right: '20px',
      bottom: '84px',
      width: '380px',
      height: 'min(620px, calc(100vh - 110px))',
      display: 'none',
      flexDirection: 'column',
      overflow: 'hidden',
      background: '#fff',
      borderRadius: '14px',
      boxShadow: '0 12px 40px rgba(0,0,0,.24)',
      zIndex: zIndex,
      opacity: '0',
      transform: 'translateY(12px)',
      transition: 'opacity .18s ease, transform .18s ease',
    });
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', state.title);

    document.body.appendChild(panel);
    document.body.appendChild(button);

    state.button = button;
    state.dot = dot;
    state.panel = panel;
    applyPosition();
  }

  window.addEventListener('message', function (event) {
    if (!state.iframe || event.source !== state.iframe.contentWindow) return;
    var data = event.data || {};
    if (data.type === 'cs:widget:close') close();
    if (data.type === 'cs:widget:done' && !state.open) {
      state.unread += 1;
      paintDot();
    }
  });

  window.addEventListener('resize', applyPosition);

  build();
  state.button.style.display = 'none';

  // 读取公开配置：主题/标题/位置以后台为准，失败时退回 data-* 属性
  fetch(
    apiBase + '/api/v1/public/scenes/' + encodeURIComponent(scene),
    integration ? { headers: { 'X-Integration-Code': integration } } : undefined,
  )
    .then(function (resp) {
      return resp.ok ? resp.json() : null;
    })
    .then(function (body) {
      var cfg = body && body.data && body.data.widget;
      if (!cfg) return;
      if (cfg.title) {
        state.title = cfg.title;
        state.iframe && state.iframe.setAttribute('title', cfg.title);
      }
      if (cfg.theme_color) {
        state.color = cfg.theme_color;
        state.button.style.background = cfg.theme_color;
      }
      if (cfg.position === 'left' || cfg.position === 'right') {
        state.position = cfg.position;
        applyPosition();
      }
    })
    .catch(function () {
      /* 配置读取失败不阻塞气泡：iframe 内会展示具体错误 */
    })
    .finally(function () {
      state.button.style.display = 'flex';
    });

  window.AgentDockWidget = {
    open: open,
    close: close,
    toggle: toggle,
    scene: scene,
    integration: integration || null,
    ext: ext || null,
  };
})();
