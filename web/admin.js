/* 관리자 패널. 계약: docs/api-and-admin.md 3, 4, 6절.
   CSRF 토큰은 로그인 응답에서 받아 변경 요청(PUT/POST/DELETE)의 X-CSRF-Token 헤더로 보낸다.
   새로 고침해도 세션이 이어지도록 토큰을 sessionStorage 에 둔다(탭을 닫으면 사라짐). */
(function () {
  'use strict';
  var P = window.P2M, el = P.el, clear = P.clear;
  var $ = function (id) { return document.getElementById(id); };
  var PROVIDERS = [['anthropic', 'Anthropic'], ['openai', 'OpenAI']];
  var KEY = 'p2m_csrf';
  var csrf = null;
  try { csrf = sessionStorage.getItem(KEY); } catch (_) { csrf = null; }

  function setCsrf(t) {
    csrf = t;
    try { if (t) sessionStorage.setItem(KEY, t); else sessionStorage.removeItem(KEY); } catch (_) {}
  }
  function view(name) {
    ['loading', 'setup', 'login', 'panel'].forEach(function (v) {
      $('view-' + v).classList.toggle('hidden', v !== name);
    });
  }
  function note(text) { $('msg').textContent = text || ''; }
  function fail(err) { P.showError($('error'), err); }
  function clearError() { $('error').classList.add('hidden'); }

  // 변경 요청. 401 이면 로그인 화면으로 돌아간다.
  function mut(method, path, body) {
    clearError();
    return P.api(method, path, body, { csrf: csrf }).catch(function (e) {
      if (e.status === 401) { setCsrf(null); view('login'); }
      throw e;
    });
  }

  function start() {
    clearError();
    view('loading');
    P.api('GET', '/api/admin/status').then(function (s) {
      if (!s.configured) { view('setup'); return; }
      return loadSettings();
    }).catch(fail);
  }

  function loadSettings() {
    return P.api('GET', '/api/admin/settings').then(function (s) {
      render(s);
      view('panel');
    }, function (e) {
      if (e.status === 401) { setCsrf(null); view('login'); return; }
      throw e;
    });
  }

  // ---- 최초 설정 / 로그인 ----
  $('setup-form').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var pass = $('setup-pass').value;
    if (pass.length < 12) return fail({ message: '비밀번호는 12자 이상이어야 합니다.' });
    if (pass !== $('setup-pass2').value) return fail({ message: '비밀번호 확인이 일치하지 않습니다.' });
    mut('POST', '/api/admin/setup', { setup_token: $('setup-token').value, password: pass }).then(function () {
      $('setup-token').value = ''; $('setup-pass').value = ''; $('setup-pass2').value = '';
      note('설정이 끝났습니다. 로그인해 주세요.');
      view('login');
      $('login-pass').focus();
    }).catch(fail);
  });

  $('login-form').addEventListener('submit', function (ev) {
    ev.preventDefault();
    mut('POST', '/api/admin/login', { password: $('login-pass').value }).then(function (r) {
      $('login-pass').value = '';
      setCsrf(r && r.csrf_token);
      note('');
      return loadSettings();
    }).catch(fail);
  });

  $('logout').addEventListener('click', function () {
    mut('POST', '/api/admin/logout').catch(function () {}).then(function () {
      setCsrf(null); note('로그아웃했습니다.'); view('login');
    });
  });

  // ---- 설정 화면 ----
  function render(s) {
    $('banner-plain').classList.toggle('hidden', !!(s.storage && s.storage.secrets_encrypted));
    var box = $('providers');
    clear(box);
    PROVIDERS.forEach(function (p) { box.appendChild(providerCard(p[0], p[1], (s.providers || {})[p[0]] || {})); });

    $('public-llm').checked = !!s.public_llm;
    var sel = $('default-provider');
    clear(sel);
    [['rule', 'Rule (offline)']].concat(PROVIDERS).forEach(function (p) {
      sel.appendChild(el('option', { value: p[0], text: p[1] }));
    });
    sel.value = s.default_provider || 'rule';

    var l = s.limits || {};
    $('lim-rpm').value = l.requests_per_minute;
    $('lim-daily').value = l.daily_llm_cap;
    $('lim-chars').value = l.max_prompt_chars;
    $('lim-retries').value = l.max_retries;
  }

  function providerCard(id, label, cfg) {
    var card = el('section', { class: 'card', 'aria-labelledby': 'h-' + id });
    var keyState = cfg.key_set
      ? el('span', { class: 'status-ok', text: '키 등록됨 ' + (cfg.key_hint || '') })
      : el('span', { class: 'status-bad', text: '키 없음' });
    card.appendChild(el('div', { class: 'provider-head' }, [el('h3', { id: 'h-' + id, text: label }), keyState]));

    var keyInput = el('input', { id: 'key-' + id, type: 'password', autocomplete: 'new-password', placeholder: cfg.key_set ? '새 키로 바꾸려면 입력' : 'API 키 붙여넣기', spellcheck: 'false' });
    var modelInput = el('input', { id: 'model-' + id, type: 'text', autocomplete: 'off', spellcheck: 'false' });
    modelInput.value = cfg.model || '';
    var enabled = el('input', { id: 'en-' + id, type: 'checkbox' });
    enabled.checked = !!cfg.enabled;
    var out = el('p', { class: 'hint', role: 'status', 'aria-live': 'polite' });

    card.appendChild(el('label', { for: 'key-' + id, text: 'API 키' }));
    card.appendChild(keyInput);
    card.appendChild(el('p', { class: 'hint', text: '저장하면 다시 표시되지 않습니다(끝 4자 힌트만). 비워 두면 기존 키를 유지합니다.' }));
    card.appendChild(el('label', { for: 'model-' + id, text: '모델 이름' }));
    card.appendChild(modelInput);
    card.appendChild(el('p', { class: 'hint', text: '비워 두면 이 공급자는 사용할 수 없습니다.' }));
    card.appendChild(el('label', { class: 'inline-label' }, [enabled, '사용 (켜기)']));

    var save = el('button', { type: 'button', text: '저장' });
    var test = el('button', { type: 'button', class: 'secondary', text: '연결 테스트' });
    var del = el('button', { type: 'button', class: 'danger', text: '키 삭제' });
    card.appendChild(el('div', { class: 'actions' }, [save, test, del]));
    card.appendChild(el('p', { class: 'hint', text: '연결 테스트는 실제 API 를 1회 호출하므로 소액의 비용이 발생할 수 있습니다.' }));
    card.appendChild(out);

    save.addEventListener('click', function () {
      var body = { model: modelInput.value.trim(), enabled: enabled.checked };
      if (keyInput.value !== '') body.api_key = keyInput.value;
      save.disabled = true;
      mut('PUT', '/api/admin/providers/' + id, body).then(function () {
        keyInput.value = '';
        return loadSettings();
      }).then(function () { note(label + ' 설정을 저장했습니다.'); }).catch(fail)
        .then(function () { save.disabled = false; });
    });

    del.addEventListener('click', function () {
      if (!window.confirm(label + ' 의 저장된 키를 삭제할까요?')) return;
      mut('DELETE', '/api/admin/providers/' + id + '/key').then(loadSettings)
        .then(function () { note(label + ' 키를 삭제했습니다.'); }).catch(fail);
    });

    test.addEventListener('click', function () {
      if (!window.confirm(label + ' 에 테스트 요청을 1회 보냅니다. 소액의 비용이 발생할 수 있습니다. 계속할까요?')) return;
      test.disabled = true;
      out.textContent = '테스트 중…';
      mut('POST', '/api/admin/providers/' + id + '/test').then(function (r) {
        var ms = r && typeof r.latency_ms === 'number' ? ' (' + r.latency_ms + ' ms)' : '';
        var detail = r && (r.message || r.error) ? ' ' + (r.message || (r.error && r.error.message) || r.error) : '';
        out.textContent = (r && r.ok ? '성공' : '실패') + ms + detail;
      }).catch(function (e) { out.textContent = ''; fail(e); })
        .then(function () { test.disabled = false; });
    });
    return card;
  }

  $('public-form').addEventListener('submit', function (ev) {
    ev.preventDefault();
    mut('PUT', '/api/admin/public', { public_llm: $('public-llm').checked, default_provider: $('default-provider').value })
      .then(loadSettings).then(function () { note('공개 설정을 저장했습니다.'); }).catch(fail);
  });

  $('limits-form').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var f = [['requests_per_minute', 'lim-rpm'], ['daily_llm_cap', 'lim-daily'], ['max_prompt_chars', 'lim-chars'], ['max_retries', 'lim-retries']];
    var body = {};
    for (var i = 0; i < f.length; i++) {
      var raw = $(f[i][1]).value.trim(), n = Number(raw);
      if (raw === '' || !Number.isInteger(n)) return fail({ message: '제한값은 모두 정수로 입력해 주세요.' });
      body[f[i][0]] = n;
    }
    mut('PUT', '/api/admin/limits', body).then(loadSettings)
      .then(function () { note('제한값을 저장했습니다.'); }).catch(fail);
  });

  start();
})();
