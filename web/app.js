/* 공개 화면. 계약: docs/api-and-admin.md 2절, 6절 */
(function () {
  'use strict';
  var P = window.P2M, el = P.el, clear = P.clear;
  var $ = function (id) { return document.getElementById(id); };

  var prompt = $('prompt'), select = $('provider'), submit = $('submit');

  prompt.addEventListener('input', function () {
    $('prompt-count').textContent = prompt.value.length + '자';
  });

  function loadProviders() {
    return P.api('GET', '/api/providers').then(function (list) {
      clear(select);
      var avail = (Array.isArray(list) ? list : []).filter(function (p) { return p && p.available === true; });
      avail.forEach(function (p) {
        select.appendChild(el('option', { value: String(p.id), text: String(p.label || p.id) }));
      });
      if (!avail.length) {
        select.disabled = true;
        $('provider-hint').textContent = '지금 쓸 수 있는 공급자가 없습니다.';
        submit.disabled = true;
      } else {
        select.disabled = false;
        submit.disabled = false;
        $('provider-hint').textContent = '';
      }
    }).catch(function (err) {
      select.disabled = true;
      submit.disabled = true;
      P.showError($('error'), err);
    });
  }

  function renderProblems(box, items, emptyText) {
    clear(box);
    if (!items || !items.length) { box.appendChild(el('p', { class: 'hint', text: emptyText })); return; }
    var ul = el('ul', { class: 'plain' });
    items.forEach(function (w) {
      var path = w && w.path ? String(w.path) : '';
      var msg = w && w.message ? String(w.message) : String(w);
      ul.appendChild(el('li', {}, [path ? el('code', { text: path }) : '', path ? ' ' : '', msg]));
    });
    box.appendChild(ul);
  }

  function renderAttempts(items) {
    var box = $('attempts');
    clear(box);
    if (!items || !items.length) { box.appendChild(el('p', { class: 'hint', text: '기록 없음' })); return; }
    var ol = el('ol', { class: 'plain' });
    items.forEach(function (a) {
      var li = el('li');
      li.appendChild(el('strong', { text: String(a.provider) + ' ' }));
      li.appendChild(el('span', { class: a.ok ? 'status-ok' : 'status-bad', text: a.ok ? '성공' : '실패' }));
      if (a.errors && a.errors.length) {
        var ul = el('ul', { class: 'plain' });
        a.errors.forEach(function (e) {
          ul.appendChild(el('li', {}, [e.path ? el('code', { text: String(e.path) }) : '', e.path ? ' ' : '', String(e.message || '')]));
        });
        li.appendChild(ul);
      }
      ol.appendChild(li);
    });
    box.appendChild(ol);
  }

  function showResult(r) {
    $('result').classList.remove('hidden');
    $('result-meta').textContent = '사용한 공급자: ' + r.provider_used + ' · 시드: ' + r.seed;
    // 다운로드 주소는 같은 출처의 /api/midi/ 아래일 때만 링크로 쓴다.
    var url = typeof r.midi_url === 'string' ? r.midi_url : '';
    var a = $('download');
    if (url.indexOf('/api/midi/') === 0) { a.setAttribute('href', url); a.classList.remove('hidden'); }
    else { a.removeAttribute('href'); a.classList.add('hidden'); }
    renderProblems($('warnings'), r.warnings, '경고 없음');
    renderAttempts(r.attempts);
    $('spec').textContent = JSON.stringify(r.spec, null, 2);
    $('result').focus();
  }

  $('gen-form').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var err = $('error');
    err.classList.add('hidden');
    var text = prompt.value;
    if (!text.trim()) {
      P.showError(err, { message: '프롬프트를 입력해 주세요.' });
      prompt.focus();
      return;
    }
    var body = { prompt: text, fallback: $('fallback').checked };
    if (select.value) body.provider = select.value;
    var seed = $('seed').value.trim();
    if (seed !== '') {
      var n = Number(seed);
      if (!Number.isInteger(n)) { P.showError(err, { message: '시드는 정수여야 합니다.' }); $('seed').focus(); return; }
      body.seed = n;
    }
    submit.disabled = true;
    $('status').textContent = '생성하는 중…';
    P.api('POST', '/api/generate', body).then(function (r) {
      $('status').textContent = '완료되었습니다.';
      showResult(r);
    }).catch(function (e) {
      $('status').textContent = '';
      P.showError(err, e);
      // 생성 실패(422) 등은 시도 이력이 details 에 온다.
      if (e.details && Array.isArray(e.details.attempts)) {
        $('result').classList.remove('hidden');
        $('result-meta').textContent = '생성에 실패했습니다.';
        $('download').classList.add('hidden');
        renderProblems($('warnings'), [], '경고 없음');
        renderAttempts(e.details.attempts);
        $('spec').textContent = '';
      }
    }).then(function () { submit.disabled = select.disabled; });
  });

  loadProviders();
})();
