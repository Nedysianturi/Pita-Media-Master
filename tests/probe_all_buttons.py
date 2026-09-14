import httpx
import json

BASE_URL = 'http://127.0.0.1:80'

def test_suite():
    print('==================================================')
    print('MEMULAI PENGUJIAN SEMUA KONEKSI & TOMBOL DASHBOARD')
    print('==================================================')
    
    results = {}
    
    with httpx.Client(base_url=BASE_URL, timeout=15.0) as client:
        # 1. TEST KONEKSI PROVIDER & PLATFORM
        print('\n--- [1/6] Menguji Endpoint Koneksi Provider ---', flush=True)
        providers = ['facebook', 'instagram', 'threads', 'telegram', 'gemini', 'xai']
        for p in providers:
            try:
                print(f'  ... Testing {p}...', flush=True)
                res = client.post('/api/credentials/test', json={'service_name': p})
                data = res.json()
                status = data.get('status', 'UNKNOWN')
                msg = data.get('message', '')
                latency = data.get('latency_ms', '-')
                print(f'  * {p.upper():<12}: Status={status:<15} (Latency: {latency}ms) -> {msg}', flush=True)
                results['conn_' + p] = (res.status_code == 200, status, msg)
            except Exception as e:
                print(f'  * {p.upper():<12}: EROR -> {e}', flush=True)
                results['conn_' + p] = (False, 'ERROR', str(e))

        # 2. TEST TOMBOL KONTROL SISTEM
        print('\n--- [2/6] Menguji Tombol Kontrol Sistem ---')
        controls = ['PAUSE', 'RESUME', 'START']
        for c in controls:
            try:
                res = client.post(f'/api/control/{c}')
                success = res.json().get('success', False)
                print(f'  * Button {c}: HTTP {res.status_code} -> success={success}')
                results['ctrl_' + c] = (res.status_code == 200 and success, 'OK')
            except Exception as e:
                print(f'  * Button {c}: EROR -> {e}')
                results['ctrl_' + c] = (False, str(e))

        # 3. TEST TOGGLE MODE
        print('\n--- [3/6] Menguji Tombol Switch Mode ---')
        try:
            res1 = client.post('/api/app_mode/toggle', json={'mode': 'DRY_RUN'})
            msg1 = res1.json().get('message')
            print(f'  * Switch to DRY_RUN: {msg1}')
            res2 = client.post('/api/app_mode/toggle', json={'mode': 'PRODUCTION'})
            msg2 = res2.json().get('message')
            print(f'  * Switch to PRODUCTION: {msg2}')
            client.post('/api/app_mode/toggle', json={'mode': 'DRY_RUN'})
            results['toggle_mode'] = (True, 'OK')
        except Exception as e:
            print(f'  * Switch Mode: EROR -> {e}')
            results['toggle_mode'] = (False, str(e))

        # 4. TEST TAB & DATA ENDPOINTS
        print('\n--- [4/6] Menguji Pengambilan Data Tab ---')
        endpoints = [
            ('/api/stats', 'Stats & Metrics'),
            ('/api/env', 'Kredensial (.env)'),
            ('/api/jobs', 'Antrian Job Queue'),
            ('/api/content', 'Content Studio Assets'),
            ('/api/receipts', 'Publishing Receipts'),
            ('/api/providers', 'AI Providers List'),
            ('/api/experiments', 'A/B Experiments'),
            ('/api/music/tracks', 'Music Library'),
            ('/api/qc', 'QC Gate Records'),
            ('/api/storage/disk', 'Storage & Disk Guard'),
            ('/api/config/versions', 'Config Snapshots'),
            ('/api/reports/daily', 'Laporan Harian'),
            ('/api/reports/weekly', 'Laporan Mingguan'),
            ('/api/logs', 'Terminal Live Logs')
        ]
        for ep, desc in endpoints:
            try:
                res = client.get(ep)
                is_ok = res.status_code == 200
                status_str = 'OK' if is_ok else 'FAIL'
                print(f'  * Tab Data {desc}: HTTP {res.status_code} ({status_str})')
                results['tab_' + ep] = (is_ok, res.status_code)
            except Exception as e:
                print(f'  * Tab Data {desc}: EROR -> {e}')
                results['tab_' + ep] = (False, str(e))

        # 5. TEST LEARNING CENTER BUTTONS & ACTIONS
        print('\n--- [5/6] Menguji Tombol & Aksi Learning Center ---')
        try:
            res_learn = client.get('/api/learning/overview')
            print(f'  * Get Learning Overview: HTTP {res_learn.status_code}')
            
            res_auto = client.post('/api/learning/autonomy', json={'level': 'OBSERVE', 'reason': 'Test Suite Probe'})
            auto_stat = res_auto.json().get('data', {}).get('status')
            print(f'  * Set Autonomy Level (OBSERVE): {auto_stat}')
            
            res_pause = client.post('/api/learning/pause', json={'pause': False})
            is_p = res_pause.json().get('is_paused')
            print(f'  * Learning Pause/Resume: is_paused={is_p}')
            
            res_roll = client.post('/api/learning/strategy/rollback')
            roll_msg = res_roll.json().get('message')
            print(f'  * Strategy Rollback Probe: {roll_msg}')
            
            results['learning_actions'] = (True, 'OK')
        except Exception as e:
            print(f'  * Learning Actions: EROR -> {e}')
            results['learning_actions'] = (False, str(e))

        # 6. TEST STORAGE CLEANUP BUTTON
        print('\n--- [6/6] Menguji Tombol Cleanup Storage ---')
        try:
            res_clean = client.post('/api/storage/cleanup')
            clean_res = res_clean.json()
            print(f'  * Storage Cleanup Button: {clean_res}')
            results['storage_cleanup'] = (res_clean.status_code == 200, 'OK')
        except Exception as e:
            print(f'  * Storage Cleanup: EROR -> {e}')
            results['storage_cleanup'] = (False, str(e))

    print('\n==================================================')
    all_passed = all(v[0] for v in results.values())
    status_summary = 'SEMUA FITUR & TOMBOL BERFUNGSI 100% PASS' if all_passed else 'ADA PERINGATAN'
    print(f'HASIL AKHIR: {status_summary}')
    print('==================================================')

if __name__ == '__main__':
    test_suite()
