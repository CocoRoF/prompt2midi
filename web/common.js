/* 공통 도우미. 동적 텍스트는 항상 textContent 로 넣는다(XSS 방지). HTML 문자열 삽입 금지. */
(function () {
  'use strict';

  function el(tag, props, children) {
    var node = document.createElement(tag);
    if (props) {
      Object.keys(props).forEach(function (k) {
        if (k === 'text') node.textContent = props[k];
        else if (k === 'class') node.className = props[k];
        else node.setAttribute(k, props[k]);
      });
    }
    (children || []).forEach(function (c) {
      node.appendChild(typeof c === 'string' ? document.createTextNode(c) : c);
    });
    return node;
  }

  function clear(node) {
    while (node.firstChild) node.removeChild(node.firstChild);
  }

  // 서버 오류 형식 {"error":{code,message,details}} 를 그대로 Error 에 담는다.
  function ApiError(status, body) {
    var e = body && body.error ? body.error : {};
    this.status = status;
    this.code = e.code || 'http_' + status;
    this.apiMessage = typeof e.message === 'string' ? e.message : '';
    this.message = this.apiMessage || ('요청이 실패했습니다 (HTTP ' + status + ')');
    this.details = e.details;
  }
  ApiError.prototype = Object.create(Error.prototype);

  // api(method, path, body, opts) -> Promise(json)
  function api(method, path, body, opts) {
    opts = opts || {};
    var headers = { Accept: 'application/json' };
    var init = { method: method, headers: headers, credentials: 'same-origin' };
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    if (opts.csrf) headers['X-CSRF-Token'] = opts.csrf;
    return fetch(path, init).then(function (res) {
      return res.text().then(function (text) {
        var data = null;
        if (text) { try { data = JSON.parse(text); } catch (_) { data = null; } }
        if (!res.ok) throw new ApiError(res.status, data);
        return data;
      });
    }, function () {
      throw new ApiError(0, { error: { code: 'network', message: '서버에 연결하지 못했습니다.' } });
    });
  }

  function showError(box, err) {
    clear(box);
    box.classList.remove('hidden');
    box.appendChild(el('strong', { text: err.message }));
    if (err.code) box.appendChild(el('div', { class: 'hint', text: '코드: ' + err.code }));
    return box;
  }

  window.P2M = { el: el, clear: clear, api: api, ApiError: ApiError, showError: showError };
})();
