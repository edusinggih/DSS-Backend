# DSS v32 — Dashboard & Reporting

## Frontend
- Menambahkan tombol Export Excel dan Export PDF pada area Dashboard KPI.
- Menambahkan perbandingan performa departemen yang dihitung dari response leaderboard global yang sama.
- Mempertahankan podium, ranking, foto profil, filter periode, dan tren poin.
- Service Worker cache version dinaikkan ke `dss-app-v32` agar aset rilis ini dikenali sebagai versi baru.

## Backend
- Menambahkan blueprint `/api/reports`.
- `GET /api/reports/suggestions.xlsx` menghasilkan workbook XLSX.
- `GET /api/reports/suggestions.pdf` menghasilkan laporan PDF.
- Keduanya menggunakan autentikasi token.
- Admin dapat melihat seluruh suggestion; pengguna non-Admin hanya suggestion yang diajukan atau yang melibatkan dirinya sebagai contributor.
- Filter `period=all|month|year`, `year`, dan `month` mengikuti pilihan periode Dashboard.
- Poin laporan dibaca dari ledger `ss_kpi_points`; tidak menghitung poin dari status secara manual.
- Menambahkan dependensi `openpyxl` dan `reportlab`.

## Instalasi
1. Backup folder DSS-Backend dan DSS-Frontend.
2. Replace kedua folder dengan isi ZIP.
3. Aktifkan `.venv` di DSS-Backend.
4. Jalankan `pip install -r requirements.txt`.
5. Jalankan `python run.py`.
6. Buka frontend lokal dan refresh satu kali.
7. Uji Export Excel dan Export PDF dari Dashboard.

## Validasi
- `reports.py` dan `app/__init__.py` lolos Python compile check.
- `assets/js/app.js` lolos `node --check`.
- Belum dilakukan runtime test Flask karena interpreter kerja ini tidak memiliki Flask/flask-cors terpasang dan tidak terhubung ke database lokal pengguna.
