import pandas as pd
import numpy as np
import json
import matplotlib.pyplot as plt
from matplotlib import rcParams

# Türkçe karakter sorunu çözümü
rcParams['font.family'] = 'DejaVu Sans'
rcParams['axes.unicode_minus'] = False

print("🎯 AKILLI EV ENERJİ YÖNETİMİ - PROJE SİMÜLASYONU")
print("=" * 60)

# 1. VERİLERİ YÜKLE
print("📁 Veri dosyaları yükleniyor...")
devices_df = pd.read_csv('devices.csv')
tou_df = pd.read_csv('tou_tariffs.csv')
pv_df = pd.read_csv('pv_generation.csv')

with open('system_parameters.json', 'r') as f:
    params = json.load(f)

print(f"✅ {len(devices_df)} cihaz, TOU tarifesi ve PV verisi yüklendi")

# 2. TEMEL FONKSİYONLAR
def calculate_baseline_load(hour, devices_df):
    """Temel yük hesaplama"""
    total_load = 0
    for _, device in devices_df.iterrows():
        if device['category'] == 'critical':
            total_load += device['power_kw']
        elif device['category'] in ['shiftable', 'optional']:
            start_h = int(device['start_earliest'][:2])
            end_h = int(device['end_latest'][:2])
            if start_h < end_h:
                if start_h <= hour < end_h:
                    total_load += device['power_kw']
            else:
                if hour >= start_h or hour < end_h:
                    total_load += device['power_kw']
    return total_load

def get_tou_rate(hour, tou_df):
    """TOU tarifesi bul"""
    current_time = f"{hour:02d}:00"
    for _, period in tou_df.iterrows():
        start, end = period['start_time'], period['end_time']
        if start <= end:
            if start <= current_time < end:
                return period
        else:
            if current_time >= start or current_time < end:
                return period
    return tou_df.iloc[0]

# 3. GELİŞMİŞ ENERJİ YÖNETİM ALGORİTMASI
def advanced_energy_management(hours, devices_df, pv_df, tou_df, params, season='summer'):
    """Geliştirilmiş kural tabanlı algoritma"""
    
    results = {
        'hour': hours,
        'baseline_load': [],
        'final_load': [],
        'pv_generation': [],
        'grid_import': [],
        'grid_export': [],
        'battery_soc': [],
        'ev_soc': [],
        'ac_setpoint': [],
        'cost': [],
        'cost_baseline': []  # Algoritma öncesi maliyet
    }
    
    # Başlangıç durumları
    battery_soc = params['battery']['soc_initial']
    ev_soc = params['ev']['soc_initial']
    ac_setpoint = params['ac']['comfort_max_summer']
    
    # PV verisi
    pv_generation = pv_df[f'{season}_kw'].tolist()
    
    for hour in hours:
        # A. BAŞLANGIÇ YÜKÜ (Algoritma Öncesi)
        baseline_load = calculate_baseline_load(hour, devices_df)
        ac_power_baseline = params['ac']['nominal_power_kw']
        total_load_baseline = baseline_load + ac_power_baseline
        
        # B. PV ÖNCELİK SIRASI UYGULA
        pv_power = pv_generation[hour]
        remaining_pv = pv_power
        
        # 1. Öncelik: Lokal tüketim
        net_load_after_pv = total_load_baseline - remaining_pv
        if net_load_after_pv < 0:
            remaining_pv = -net_load_after_pv
            net_load_after_pv = 0
        else:
            remaining_pv = 0
        
        # 2. Öncelik: Batarya şarj
        if remaining_pv > 0 and battery_soc < params['battery']['soc_max']:
            charge_power = min(remaining_pv, params['battery']['max_charge_rate_kw'],
                             (params['battery']['soc_max'] - battery_soc) * params['battery']['capacity_kwh'])
            battery_soc += charge_power / params['battery']['capacity_kwh'] * params['battery']['efficiency']
            remaining_pv -= charge_power
        
        # 3. Öncelik: EV şarj
        ev_plugged_in = (hour >= 18 or hour < 7)
        if remaining_pv > 0 and ev_plugged_in and ev_soc < params['ev']['soc_required_morning']:
            charge_power = min(remaining_pv, params['ev']['max_charge_rate_kw'],
                             (params['ev']['soc_required_morning'] - ev_soc) * params['ev']['capacity_kwh'])
            ev_soc += charge_power / params['ev']['capacity_kwh'] * params['ev']['efficiency_charge']
            remaining_pv -= charge_power
        
        # 4. Kalan PV şebekeye
        grid_export_pv = remaining_pv
        
        # C. AKILLI YÖNETİM KURALLARI
        current_tou = get_tou_rate(hour, tou_df)
        
        # AC Yük Optimizasyonu
        ac_power = params['ac']['nominal_power_kw']
        if net_load_after_pv > params['load_threshold_kw']:
            ac_setpoint = min(26.0, ac_setpoint + 0.5)
            savings = params['ac']['savings_percentage_per_degree']
            degrees_above = ac_setpoint - params['ac']['comfort_max_summer']
            ac_power = max(0.5, params['ac']['nominal_power_kw'] * (1 - savings * degrees_above))
        
        total_load_final = baseline_load + ac_power
        net_load_final = total_load_final - pv_power + grid_export_pv
        
        # Batarya Yönetimi
        if net_load_final > 0 and current_tou['period'] == 'Peak' and battery_soc > params['battery']['soc_min'] + 0.1:
            discharge_power = min(net_load_final, params['battery']['max_discharge_rate_kw'],
                                (battery_soc - params['battery']['soc_min']) * params['battery']['capacity_kwh'])
            net_load_final -= discharge_power
            battery_soc -= discharge_power / params['battery']['capacity_kwh']
        
        # EV Yönetimi (V2G)
        if ev_plugged_in and net_load_final > 0 and current_tou['period'] == 'Peak' and ev_soc > params['ev']['soc_min'] + 0.1:
            discharge_power = min(net_load_final, params['ev']['max_discharge_rate_kw'],
                                (ev_soc - params['ev']['soc_min']) * params['ev']['capacity_kwh'])
            net_load_final -= discharge_power
            ev_soc -= discharge_power / params['ev']['capacity_kwh']
        
        # D. SONUÇLARI KAYDET
        results['baseline_load'].append(total_load_baseline)
        results['final_load'].append(max(0, net_load_final))
        results['pv_generation'].append(pv_power)
        results['grid_import'].append(max(0, net_load_final))
        results['grid_export'].append(max(0, grid_export_pv))
        results['battery_soc'].append(battery_soc)
        results['ev_soc'].append(ev_soc)
        results['ac_setpoint'].append(ac_setpoint)
        
        # Maliyet hesapla
        current_rate = current_tou['rate_per_kwh_tl']
        results['cost'].append(max(0, net_load_final) * current_rate)
        results['cost_baseline'].append(max(0, total_load_baseline - pv_power) * current_rate)
    
    return results

# 4. GÖRSELLEŞTİRME (Ödev gereksinimlerine uygun)
def create_final_plots(results, params, season='summer'):
    """Ödev gereksinimlerine uygun görseller"""
    
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
    
    season_tr = 'Yaz' if season == 'summer' else 'Kis'
    fig.suptitle(f'Akilli Ev Enerji Yonetimi - {season_tr} Sezonu', fontsize=16, fontweight='bold')
    
    # 1. YÜK KARŞILAŞTIRMASI (Before vs After)
    ax1.plot(results['hour'], results['baseline_load'], 'r-', label='Algoritma Oncesi', linewidth=2)
    ax1.plot(results['hour'], results['final_load'], 'b-', label='Algoritma Sonrasi', linewidth=2)
    ax1.plot(results['hour'], results['pv_generation'], 'g-', label='PV Uretimi', linewidth=2, alpha=0.7)
    ax1.axhline(y=params['load_threshold_kw'], color='k', linestyle='--', label='Yuk Esigi', alpha=0.7)
    ax1.set_xlabel('Saat')
    ax1.set_ylabel('Guc (kW)')
    ax1.set_title('Yuk Karsilastirmasi (Oncesi vs Sonrasi)')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.set_xticks(range(0, 24, 2))
    
    # 2. BATARYA ve EV DURUMU
    ax2.plot(results['hour'], [x*100 for x in results['battery_soc']], 'orange', label='Batarya SOC', linewidth=2)
    ax2.plot(results['hour'], [x*100 for x in results['ev_soc']], 'purple', label='EV SOC', linewidth=2)
    ax2.axhline(y=params['battery']['soc_min']*100, color='orange', linestyle='--', alpha=0.5)
    ax2.axhline(y=params['ev']['soc_min']*100, color='purple', linestyle='--', alpha=0.5)
    ax2.axhline(y=params['ev']['soc_required_morning']*100, color='red', linestyle='--', label='EV Hedef SOC')
    ax2.set_xlabel('Saat')
    ax2.set_ylabel('SOC (%)')
    ax2.set_title('Batarya ve EV Durumu')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.set_xticks(range(0, 24, 2))
    ax2.set_ylim(0, 100)
    
    # 3. ŞEBEKE ETKİLEŞİMİ
    hours = results['hour']
    ax3.bar(hours, results['grid_import'], alpha=0.7, label='Sebekeden Cekilen', color='red')
    ax3.bar(hours, [-x for x in results['grid_export']], alpha=0.7, label='Sebekeye Verilen', color='green')
    ax3.set_xlabel('Saat')
    ax3.set_ylabel('Guc (kW)')
    ax3.set_title('Sebeke Etkilesimi')
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    ax3.set_xticks(range(0, 24, 2))
    
    # 4. KONFOR ANALİZİ
    ax4.plot(results['hour'], results['ac_setpoint'], 'b-', label='AC Setpoint', linewidth=2)
    ax4.axhline(y=params['ac']['comfort_min_summer'], color='r', linestyle='--', label='Konfor Alt Sinir', alpha=0.7)
    ax4.axhline(y=params['ac']['comfort_max_summer'], color='r', linestyle='--', label='Konfor Ust Sinir', alpha=0.7)
    ax4.fill_between(results['hour'], params['ac']['comfort_min_summer'], params['ac']['comfort_max_summer'], 
                     alpha=0.2, color='green', label='Konfor Bandi')
    ax4.set_xlabel('Saat')
    ax4.set_ylabel('Sicaklik (°C)')
    ax4.set_title('Konfor Analizi')
    ax4.legend()
    ax4.grid(True, alpha=0.3)
    ax4.set_xticks(range(0, 24, 2))
    ax4.set_ylim(20, 27)
    
    plt.tight_layout()
    plt.savefig(f'final_results_{season}.png', dpi=300, bbox_inches='tight')
    plt.show()

# 5. DETAYLI RAPOR
def generate_comprehensive_report(results, params, season='summer'):
    """Kapsamlı rapor oluştur"""
    
    season_tr = 'YAZ' if season == 'summer' else 'KIS'
    print(f"\n{'='*60}")
    print(f"📊 KAPSAMLI RAPOR - {season_tr} SEZONU")
    print(f"{'='*60}")
    
    # Temel metrikler
    cost_before = sum(results['cost_baseline'])
    cost_after = sum(results['cost'])
    savings = cost_before - cost_after
    savings_percent = (savings / cost_before) * 100
    
    peak_before = max(results['baseline_load'])
    peak_after = max(results['final_load'])
    peak_reduction = peak_before - peak_after
    peak_reduction_percent = (peak_reduction / peak_before) * 100
    
    comfort_violations = sum(1 for temp in results['ac_setpoint'] if temp > params['ac']['comfort_max_summer'])
    ev_target_met = results['ev_soc'][7] >= params['ev']['soc_required_morning']
    
    print(f"\n💰 MALIYET ANALIZI:")
    print(f"   • Algoritma Oncesi: {cost_before:.2f} TL")
    print(f"   • Algoritma Sonrasi: {cost_after:.2f} TL")
    print(f"   • Tasarruf: {savings:.2f} TL (%{savings_percent:.1f})")
    
    print(f"\n⚡ TEPE YUK ANALIZI:")
    print(f"   • Oncesi: {peak_before:.2f} kW")
    print(f"   • Sonrasi: {peak_after:.2f} kW") 
    print(f"   • Azalma: {peak_reduction:.2f} kW (%{peak_reduction_percent:.1f})")
    
    print(f"\n🎯 KONFOR ve HEDEFLER:")
    print(f"   • Konfor Ihlal Saati: {comfort_violations} saat")
    print(f"   • EV Hedef SOC: {'✅ BASARILI' if ev_target_met else '❌ BASARISIZ'}")
    print(f"   • Ortalama AC Setpoint: {np.mean(results['ac_setpoint']):.1f}°C")
    
    print(f"\n🔋 ENERJI DURUMU:")
    print(f"   • Toplam Tuketim: {sum(results['baseline_load']):.1f} kWh")
    print(f"   • PV Uretimi: {sum(results['pv_generation']):.1f} kWh")
    print(f"   • Kendine Yeterlilik: {sum(results['pv_generation'])/sum(results['baseline_load'])*100:.1f}%")

# 6. ANA SİMÜLASYON
print(f"\n🔧 GELIŞMİŞ ALGORITMA CALISTIRILIYOR...")

hours = list(range(24))

# Yaz sezonu
print(f"\n🌞 YAZ SEZONU ANALIZI...")
results_summer = advanced_energy_management(hours, devices_df, pv_df, tou_df, params, 'summer')
create_final_plots(results_summer, params, 'summer')
generate_comprehensive_report(results_summer, params, 'summer')

# Kış sezonu  
print(f"\n❄️  KIS SEZONU ANALIZI...")
results_winter = advanced_energy_management(hours, devices_df, pv_df, tou_df, params, 'winter')
create_final_plots(results_winter, params, 'winter')
generate_comprehensive_report(results_winter, params, 'winter')

print(f"\n🎉 PROJE TAMAMLANDI!")
print(f"📁 'final_results_summer.png' ve 'final_results_winter.png' dosyalari olusturuldu.")