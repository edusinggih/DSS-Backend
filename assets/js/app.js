(() => {
  const form = document.getElementById('loginForm');
  const usernameInput = document.getElementById('username');
  const passwordInput = document.getElementById('password');
  const message = document.getElementById('message');
  const submitButton = form.querySelector('button');
  const apiBase = window.DSS_CONFIG?.API_BASE_URL;

  function showMessage(text, type = 'error') {
    message.textContent = text;
    message.style.color = type === 'success' ? '#16836b' : '#a65b00';
  }

  if (!apiBase) {
    showMessage('Konfigurasi server belum tersedia.');
    return;
  }

  form.addEventListener('submit', async (event) => {
    event.preventDefault();

    const identity = usernameInput.value.trim();
    const password = passwordInput.value;

    if (!identity || !password) {
      showMessage('Username dan password wajib diisi.');
      return;
    }

    submitButton.disabled = true;
    submitButton.textContent = 'Memeriksa...';
    showMessage('Menghubungkan ke server DSS...', 'success');

    try {
      const loginResponse = await fetch(apiBase + '/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ identity, password })
      });

      const loginData = await loginResponse.json().catch(() => ({}));

      if (!loginResponse.ok || !loginData.success || !loginData.token) {
        throw new Error(loginData.message || 'Login gagal. Periksa username dan password.');
      }

      sessionStorage.setItem('dss_token', loginData.token);

      const meResponse = await fetch(apiBase + '/auth/me', {
        method: 'GET',
        headers: { 'Authorization': 'Bearer ' + loginData.token }
      });

      const meData = await meResponse.json().catch(() => ({}));

      if (!meResponse.ok || !meData.success || !meData.user) {
        sessionStorage.removeItem('dss_token');
        throw new Error(meData.message || 'Token tidak dapat diverifikasi.');
      }

      const user = meData.user;
      passwordInput.value = '';
      showMessage(
        'Login berhasil. Selamat datang, ' + user.full_name + ' (' + user.role + ').',
        'success'
      );
    } catch (error) {
      sessionStorage.removeItem('dss_token');
      showMessage(error.message || 'Tidak dapat terhubung ke server DSS.');
    } finally {
      submitButton.disabled = false;
      submitButton.textContent = 'Masuk →';
    }
  });
})();
