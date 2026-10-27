import RFB from './core/rfb.js';

const status = document.getElementById('status');
const control = document.getElementById('ctrl-alt-del');
const parameters = new URLSearchParams(window.location.search);
let token = parameters.get('token');
const path = parameters.get('path');
if (path) {
  try { token = new URL(path, window.location.origin).searchParams.get('token'); }
  catch { token = null; }
}
if (!token || token.length > 512 || !/^[A-Za-z0-9._~-]+$/.test(token)) {
  status.textContent = 'Console authorization is missing. Open the console from your instance page.';
} else {
  const endpoint = new URL('./websockify', window.location.href);
  endpoint.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  endpoint.search = new URLSearchParams({ token });
  const connection = new RFB(document.getElementById('screen'), endpoint.href);
  connection.scaleViewport = true;
  connection.viewOnly = false;
  connection.addEventListener('connect', () => {
    status.textContent = 'Connected';
    control.disabled = false;
  });
  connection.addEventListener('disconnect', () => {
    status.textContent = 'Console disconnected. Reopen it from the instance page.';
    control.disabled = true;
  });
  connection.addEventListener('securityfailure', () => {
    status.textContent = 'Console authorization failed. Request a fresh console from your instance page.';
  });
  control.addEventListener('click', () => connection.sendCtrlAltDel());
}
