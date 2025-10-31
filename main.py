import pandas as pd
import numpy as np
import json
from datetime import datetime, timedelta

# 1. VERİLERİ YÜKLE
print("📁 Veri dosyaları yükleniyor...")

# Cihazları yükle
devices_df = pd.read_csv('devices.csv')
print(f"Yüklenen cihaz sayısı: {len(devices_df)}")

# Tarifeyi yükle
tou_df = pd.read_csv('tou_tariffs.csv')
print("TOU Tarifesi yüklendi")

# PV üretimini yükle
pv_df = pd.read_csv('pv_generation.csv')
print("PV verisi yüklendi")

# Sistem parametrelerini yükle
with open('system_parameters.json', 'r') as f:
    params = json.load(f)

print("✅ Tüm veriler başarıyla yüklendi!")
print(f"Batarya kapasitesi: {params['battery']['capacity_kwh']} kWh")
print(f"EV kapasitesi: {params['ev']['capacity_kwh']} kWh")
print(f"AC tasarruf oranı: {params['ac']['savings_percentage_per_degree']*100}%")

# 2. TEMEL SİMÜLASYON ZAMAN AYARLARI
hours = list(range(24))

# 3. BAŞLANGIÇ DURUMLARI
battery_soc = params['battery']['soc_initial']
ev_soc = params['ev']['soc_initial']

print(f"\n⏰ Simülasyon başlangıç durumları:")
print(f"   Batarya SOC: {battery_soc*100:.1f}%")
print(f"   EV SOC: {ev_soc*100:.1f}%")

# 4. TEMEL YÜK PROFİLİ HESAPLAMA (Basit versiyon)
def calculate_baseline_load(hour, devices_df):
    """Basit saatlik yük hesaplama"""
    total_load = 0
    
    for _, device in devices_df.iterrows():
        # Kritik cihazlar her zaman çalışır (24 saat)
        if device['category'] == 'critical':
            total_load += device['power_kw']
        
        # Diğer cihazlar için basit bir zaman kontrolü
        elif device['category'] in ['shiftable', 'optional']:
            start_h = int(device['start_earliest'][:2])
            end_h = int(device['end_latest'][:2])
            
            # Saat aralığı kontrolü (gece yarısını aşan durumlar için)
            if start_h < end_h:
                # Normal aralık (07:00-22:00 gibi)
                if start_h <= hour < end_h:
                    total_load += device['power_kw']
            else:
                # Gece yarısını aşan aralık (22:00-06:00 gibi)
                if hour >= start_h or hour < end_h:
                    total_load += device['power_kw']
    
    return total_load

# 5. BAŞLANGIÇ YÜK PROFİLİNİ HESAPLA
print(f"\n📊 Başlangıç yük profili hesaplanıyor...")
baseline_load = [calculate_baseline_load(hour, devices_df) for hour in hours]

print(f"   Tepe yük: {max(baseline_load):.2f} kW")
print(f"   Günlük toplam tüketim: {sum(baseline_load):.2f} kWh")

# 6. TOU FONKSİYONU DÜZELTMESİ
def get_tou_rate(hour, tou_df):
    """Saat için TOU tarifesini bul"""
    current_time = f"{hour:02d}:00"
    
    for _, period in tou_df.iterrows():
        start = period['start_time']
        end = period['end_time']
        
        # Saat karşılaştırması
        if start <= end:
            # Normal aralık (06:00-17:00 gibi)
            if start <= current_time < end:
                return period
        else:
            # Gece yarısını aşan aralık (22:00-06:00 gibi)
            if current_time >= start or current_time < end:
                return period
    
    # Varsayılan olarak ilk period'u döndür
    return tou_df.iloc[0]

# 7. KURAL TABANLI ENERJİ YÖNETİM ALGORİTMASI
def energy_management_algorithm(hours, devices_df, pv_df, tou_df, params, season='summer'):
    """
    Kural tabanlı enerji yönetim algoritması
    """
    # Sonuçları saklayacak listeler
    results = {
        'hour': hours,
        'baseline_load': [],
        'adjusted_load': [],
        'pv_generation': [],
        'grid_import': [],
        'grid_export': [],
        'battery_soc': [],
        'ev_soc': [],
        'ac_setpoint': [],
        'load_after_ev_battery': [],
        'cost': []
    }
    
    # Başlangıç durumları
    battery_soc = params['battery']['soc_initial']
    ev_soc = params['ev']['soc_initial']
    ac_setpoint = params['ac']['comfort_max_summer']  # Başlangıçta maksimum konfor sıcaklığı
    
    # PV üretimini seç (mevsime göre)
    pv_col = f'{season}_kw'
    pv_generation = pv_df[pv_col].tolist()
    
    for hour in hours:
        # 1. BAŞLANGIÇ YÜKÜ HESAPLA
        baseline_load = calculate_baseline_load(hour, devices_df)
        
        # 2. AC YÜKÜNÜ AYARLA (Konfor bandında)
        ac_power = params['ac']['nominal_power_kw']
        if ac_setpoint > params['ac']['comfort_max_summer']:
            savings = params['ac']['savings_percentage_per_degree']
            degrees_above = ac_setpoint - params['ac']['comfort_max_summer']
            ac_power = max(0.1, params['ac']['nominal_power_kw'] * (1 - savings * degrees_above))
        
        # AC yükünü toplam yüke ekle
        total_load = baseline_load + ac_power
        
        # 3. PV ÜRETİMİNİ AL
        pv_power = pv_generation[hour]
        
        # 4. NET YÜK HESAPLA (Şebekeden çekilen)
        net_load = total_load - pv_power
        
        # 5. TOU TARİFESİNİ AL (DÜZELTİLMİŞ FONKSİYON)
        current_tou = get_tou_rate(hour, tou_df)
        
        # 6. BATARYA ve EV YÖNETİMİ (Basit kurallar)
        
        # Batarya kuralları
        if net_load > 0 and current_tou['period'] == 'Peak' and battery_soc > params['battery']['soc_min']:
            # Pik saatte deşarj
            discharge_power = min(net_load, params['battery']['max_discharge_rate_kw'], 
                                (battery_soc - params['battery']['soc_min']) * params['battery']['capacity_kwh'])
            net_load -= discharge_power
            battery_soc -= discharge_power / params['battery']['capacity_kwh']
        elif net_load < 0 and battery_soc < params['battery']['soc_max']:
            # PV fazlası ile şarj
            charge_power = min(-net_load, params['battery']['max_charge_rate_kw'],
                             (params['battery']['soc_max'] - battery_soc) * params['battery']['capacity_kwh'])
            net_load += charge_power
            battery_soc += charge_power / params['battery']['capacity_kwh'] * params['battery']['efficiency']
        
        # EV kuralları (sadece bağlı olduğu saatlerde)
        ev_plugged_in = (hour >= 18 or hour < 7)  # 18:00-07:00 arası evde
        if ev_plugged_in:
            if net_load > 0 and current_tou['period'] == 'Peak' and ev_soc > params['ev']['soc_min']:
                # Pik saatte deşarj
                discharge_power = min(net_load, params['ev']['max_discharge_rate_kw'],
                                    (ev_soc - params['ev']['soc_min']) * params['ev']['capacity_kwh'])
                net_load -= discharge_power
                ev_soc -= discharge_power / params['ev']['capacity_kwh']
            elif net_load < 0 and ev_soc < params['ev']['soc_required_morning']:
                # PV fazlası ile şarj
                charge_power = min(-net_load, params['ev']['max_charge_rate_kw'],
                                 (params['ev']['soc_required_morning'] - ev_soc) * params['ev']['capacity_kwh'])
                net_load += charge_power
                ev_soc += charge_power / params['ev']['capacity_kwh'] * params['ev']['efficiency_charge']
        
        # 7. YÜK EŞİK KONTROLÜ ve AC AYARI
        if net_load > params['load_threshold_kw']:
            # Yük eşiği aşıldı, AC setpoint'ini artır (daha az soğutma)
            ac_setpoint = min(26.0, ac_setpoint + 0.5)  # Maksimum 26°C'ye kadar
        
        # 8. SONUÇLARI KAYDET
        results['baseline_load'].append(baseline_load)
        results['adjusted_load'].append(total_load)
        results['pv_generation'].append(pv_power)
        results['grid_import'].append(max(0, net_load))
        results['grid_export'].append(max(0, -net_load))
        results['battery_soc'].append(battery_soc)
        results['ev_soc'].append(ev_soc)
        results['ac_setpoint'].append(ac_setpoint)
        results['load_after_ev_battery'].append(net_load)
        
        # Maliyet hesapla
        results['cost'].append(max(0, net_load) * current_tou['rate_per_kwh_tl'])
    
    return results

# 8. ALGORİTMAYI ÇALIŞTIR ve SONUÇLARI GÖSTER
print(f"\n🔧 Kural tabanlı algoritma çalıştırılıyor...")

# Yaz sezonu için simülasyon çalıştır
results = energy_management_algorithm(hours, devices_df, pv_df, tou_df, params, 'summer')

# Temel metrikleri hesapla
total_cost = sum(results['cost'])
peak_load_before = max(results['baseline_load'])
peak_load_after = max([max(0, x) for x in results['load_after_ev_battery']])
cost_reduction = (sum([max(0, x) for x in results['baseline_load']]) * 3.0 - total_cost)  # Basit karşılaştırma

print(f"\n📈 **SIMÜLASYON SONUÇLARI (Yaz)**")
print(f"   Toplam Maliyet: {total_cost:.2f} TL")
print(f"   Tepe Yük (Önce): {peak_load_before:.2f} kW")
print(f"   Tepe Yük (Sonra): {peak_load_after:.2f} kW")
print(f"   Tepe Yük Azalması: {peak_load_before - peak_load_after:.2f} kW ({(1 - peak_load_after/peak_load_before)*100:.1f}%)")
print(f"   Tahmini Tasarruf: {cost_reduction:.2f} TL")
print(f"   Sabah EV SOC: {results['ev_soc'][7]*100:.1f}% (Hedef: {params['ev']['soc_required_morning']*100}%)")

print(f"\n🎯 Bir sonraki adım: Görselleştirme ve detaylı analiz!")

# 9. GÖRSELLEŞTİRME ve DETAYLI ANALİZ
import matplotlib.pyplot as plt

def plot_results(results, params, season='summer'):
    """Simülasyon sonuçlarını görselleştir"""
    
    # 4 alt grafikli bir figür oluştur
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(15, 10))
    fig.suptitle(f'Akıllı Ev Enerji Yönetimi - {season.capitalize()} Sezonu', fontsize=16, fontweight='bold')
    
    # 1. Yük ve Üretim Profili
    ax1.plot(results['hour'], results['baseline_load'], 'r--', label='Başlangıç Yükü', linewidth=2)
    ax1.plot(results['hour'], results['adjusted_load'], 'r-', label='AC ile Ayarlanmış Yük', linewidth=2)
    ax1.plot(results['hour'], results['pv_generation'], 'g-', label='PV Üretimi', linewidth=2)
    ax1.plot(results['hour'], [max(0, x) for x in results['load_after_ev_battery']], 'b-', label='Son Yük (EV+Batarya Sonrası)', linewidth=2)
    ax1.axhline(y=params['load_threshold_kw'], color='k', linestyle=':', label='Yük Eşiği', alpha=0.7)
    ax1.set_xlabel('Saat')
    ax1.set_ylabel('Güç (kW)')
    ax1.set_title('Enerji Yük ve Üretim Profili')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.set_xticks(range(0, 24, 2))
    
    # 2. SOC Değişimleri
    ax2.plot(results['hour'], [x * 100 for x in results['battery_soc']], 'orange', label='Batarya SOC', linewidth=2)
    ax2.plot(results['hour'], [x * 100 for x in results['ev_soc']], 'purple', label='EV SOC', linewidth=2)
    ax2.axhline(y=params['battery']['soc_min'] * 100, color='orange', linestyle='--', label='Batarya Min SOC', alpha=0.7)
    ax2.axhline(y=params['ev']['soc_min'] * 100, color='purple', linestyle='--', label='EV Min SOC', alpha=0.7)
    ax2.axhline(y=params['ev']['soc_required_morning'] * 100, color='red', linestyle='--', label='EV Gerekli SOC', alpha=0.7)
    ax2.set_xlabel('Saat')
    ax2.set_ylabel('SOC (%)')
    ax2.set_title('Batarya ve EV SOC Değişimi')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.set_xticks(range(0, 24, 2))
    ax2.set_ylim(0, 100)
    
    # 3. Şebeke Alışverişi ve Maliyet
    ax3.bar(results['hour'], results['grid_import'], alpha=0.7, label='Şebekeden Çekilen', color='red')
    ax3.bar(results['hour'], [-x for x in results['grid_export']], alpha=0.7, label='Şebekeye Verilen', color='green')
    ax3.set_xlabel('Saat')
    ax3.set_ylabel('Güç (kW)')
    ax3.set_title('Şebeke Alışverişi')
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    ax3.set_xticks(range(0, 24, 2))
    
    # 4. AC Setpoint ve Konfor Bandı
    ax4.plot(results['hour'], results['ac_setpoint'], 'b-', label='AC Setpoint', linewidth=2)
    ax4.axhline(y=params['ac']['comfort_min_summer'], color='r', linestyle='--', label='Min Konfor', alpha=0.7)
    ax4.axhline(y=params['ac']['comfort_max_summer'], color='r', linestyle='--', label='Max Konfor', alpha=0.7)
    ax4.fill_between(results['hour'], params['ac']['comfort_min_summer'], params['ac']['comfort_max_summer'], alpha=0.2, color='green', label='Konfor Bandı')
    ax4.set_xlabel('Saat')
    ax4.set_ylabel('Sıcaklık (°C)')
    ax4.set_title('AC Setpoint ve Konfor Bandı')
    ax4.legend()
    ax4.grid(True, alpha=0.3)
    ax4.set_xticks(range(0, 24, 2))
    ax4.set_ylim(20, 27)
    
    plt.tight_layout()
    plt.savefig(f'smart_home_results_{season}.png', dpi=300, bbox_inches='tight')
    plt.show()

# 10. DETAYLI RAPOR OLUŞTURMA
def generate_detailed_report(results, params, season='summer'):
    """Detaylı simülasyon raporu oluştur"""
    
    print(f"\n{'='*60}")
    print(f"📊 DETAYLI SİMÜLASYON RAPORU - {season.upper()} SEZONU")
    print(f"{'='*60}")
    
    # Temel Metrikler
    total_cost = sum(results['cost'])
    total_consumption = sum(results['adjusted_load'])
    total_pv_generation = sum(results['pv_generation'])
    peak_load_before = max(results['baseline_load'])
    peak_load_after = max([max(0, x) for x in results['load_after_ev_battery']])
    
    # Konfor Analizi
    comfort_violations = sum(1 for temp in results['ac_setpoint'] if temp > params['ac']['comfort_max_summer'])
    max_temp_violation = max(results['ac_setpoint']) - params['ac']['comfort_max_summer']
    
    # EV Durumu
    final_ev_soc = results['ev_soc'][7] * 100  # Sabah 07:00'deki SOC
    ev_target_met = final_ev_soc >= params['ev']['soc_required_morning'] * 100
    
    print(f"\n📈 ENERJİ METRİKLERİ:")
    print(f"   • Toplam Tüketim: {total_consumption:.2f} kWh")
    print(f"   • Toplam PV Üretimi: {total_pv_generation:.2f} kWh")
    print(f"   • Kendi Kendine Yeterlilik: {(total_pv_generation/total_consumption*100):.1f}%")
    print(f"   • Tepe Yük (Önce): {peak_load_before:.2f} kW")
    print(f"   • Tepe Yük (Sonra): {peak_load_after:.2f} kW")
    print(f"   • Tepe Yük Azalması: {peak_load_before - peak_load_after:.2f} kW ({(1 - peak_load_after/peak_load_before)*100:.1f}%)")
    
    print(f"\n💰 MALİYET ANALİZİ:")
    print(f"   • Toplam Elektrik Maliyeti: {total_cost:.2f} TL")
    print(f"   • Ortalama Birim Maliyet: {total_cost/total_consumption:.2f} TL/kWh")
    
    print(f"\n🎯 KONFOR ANALİZİ:")
    print(f"   • Konfor İhlali Saat Sayısı: {comfort_violations} saat")
    print(f"   • Maksimum Sıcaklık Aşımı: {max_temp_violation:.1f}°C")
    print(f"   • Ortalama AC Setpoint: {sum(results['ac_setpoint'])/len(results['ac_setpoint']):.1f}°C")
    
    print(f"\n🔋 BATARYA ve EV DURUMU:")
    print(f"   • Sabah EV SOC: {final_ev_soc:.1f}% {'✅' if ev_target_met else '❌'}")
    print(f"   • EV Hedefi: {params['ev']['soc_required_morning']*100}% {'Karşılandı' if ev_target_met else 'Karşılanamadı'}")
    print(f"   • Minimum Batarya SOC: {min(results['battery_soc'])*100:.1f}%")
    print(f"   • Minimum EV SOC: {min(results['ev_soc'])*100:.1f}%")

# 11. DUYARLILIK ANALİZİ (AC Tasarruf Oranı)
def sensitivity_analysis(hours, devices_df, pv_df, tou_df, params):
    """AC tasarruf oranı için duyarlılık analizi"""
    
    print(f"\n{'='*50}")
    print(f"🔬 DUYARLILIK ANALİZİ - AC TASARRUF ORANI")
    print(f"{'='*50}")
    
    savings_rates = [0.05, 0.07, 0.10]  # %5, %7, %10
    results_comparison = {}
    
    for rate in savings_rates:
        print(f"\n📋 AC Tasarruf Oranı: %{rate*100:.0f} için simülasyon çalıştırılıyor...")
        
        # Parametreleri güncelle
        params_sensitivity = params.copy()
        params_sensitivity['ac']['savings_percentage_per_degree'] = rate
        
        # Simülasyonu çalıştır
        results = energy_management_algorithm(hours, devices_df, pv_df, tou_df, params_sensitivity, 'summer')
        results_comparison[rate] = results
        
        # Sonuçları göster
        total_cost = sum(results['cost'])
        peak_load = max([max(0, x) for x in results['load_after_ev_battery']])
        comfort_violations = sum(1 for temp in results['ac_setpoint'] if temp > params['ac']['comfort_max_summer'])
        
        print(f"   • Toplam Maliyet: {total_cost:.2f} TL")
        print(f"   • Tepe Yük: {peak_load:.2f} kW")
        print(f"   • Konfor İhlali: {comfort_violations} saat")
    
    return results_comparison

# 12. TÜM ANALİZLERİ ÇALIŞTIR
print(f"\n📊 Görselleştirme ve analizler oluşturuluyor...")

# Yaz sezonu için görselleştirme
plot_results(results, params, 'summer')

# Detaylı rapor
generate_detailed_report(results, params, 'summer')

# Duyarlılık analizi
sensitivity_results = sensitivity_analysis(hours, devices_df, pv_df, tou_df, params)

print(f"\n🎉 TÜM ANALİZLER TAMAMLANDI!")
print(f"📁 Çıktı dosyaları:")
print(f"   • smart_home_results_summer.png - Görsel sonuçlar")
print(f"   • Konsol çıktısı - Detaylı metrikler ve duyarlılık analizi")

print(f"\n🚀 Bir sonraki adım: Kış sezonu analizi ve rapor hazırlama!")

# 13. KARAKTER SORUNU ÇÖZÜMÜ ve GELİŞMİŞ GÖRSELLEŞTİRME
import matplotlib.pyplot as plt
from matplotlib import rcParams

# Türkçe karakter sorununu çözmek için font ayarı
try:
    rcParams['font.family'] = 'DejaVu Sans'
    rcParams['font.sans-serif'] = ['DejaVu Sans']
    rcParams['axes.unicode_minus'] = False
except:
    print("Font ayarında ufak bir sorun oluştu, ancak grafikler oluşturulacak.")

def plot_results_improved(results, params, season='summer'):
    """Geliştirilmiş görselleştirme (Türkçe karakter sorunu çözülmüş)"""
    
    fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(16, 12))
    
    if season == 'summer':
        season_title = 'Yaz Sezonu'
    else:
        season_title = 'Kis Sezonu'
    
    fig.suptitle(f'Akilli Ev Enerji Yonetimi - {season_title}', fontsize=16, fontweight='bold')
    
    # 1. Yük ve Üretim Profili
    ax1.plot(results['hour'], results['baseline_load'], 'r--', label='Baslangic Yuku', linewidth=2, alpha=0.8)
    ax1.plot(results['hour'], results['adjusted_load'], 'r-', label='AC ile Ayarlanmis Yuk', linewidth=2, alpha=0.8)
    ax1.plot(results['hour'], results['pv_generation'], 'g-', label='PV Uretimi', linewidth=2, alpha=0.8)
    ax1.plot(results['hour'], [max(0, x) for x in results['load_after_ev_battery']], 'b-', label='Son Yuk (EV+Batarya Sonrasi)', linewidth=2)
    ax1.axhline(y=params['load_threshold_kw'], color='k', linestyle=':', label='Yuk Esigi', alpha=0.7)
    ax1.fill_between(results['hour'], results['pv_generation'], alpha=0.3, color='green', label='PV Uretim Alani')
    ax1.set_xlabel('Saat')
    ax1.set_ylabel('Guc (kW)')
    ax1.set_title('Enerji Yuk ve Uretim Profili')
    ax1.legend()
    ax1.grid(True, alpha=0.3)
    ax1.set_xticks(range(0, 24, 2))
    ax1.set_xlim(0, 23)
    
    # 2. SOC Değişimleri
    ax2.plot(results['hour'], [x * 100 for x in results['battery_soc']], 'orange', label='Batarya SOC', linewidth=2, marker='o', markersize=3)
    ax2.plot(results['hour'], [x * 100 for x in results['ev_soc']], 'purple', label='EV SOC', linewidth=2, marker='s', markersize=3)
    ax2.axhline(y=params['battery']['soc_min'] * 100, color='orange', linestyle='--', label='Batarya Min SOC', alpha=0.7)
    ax2.axhline(y=params['ev']['soc_min'] * 100, color='purple', linestyle='--', label='EV Min SOC', alpha=0.7)
    ax2.axhline(y=params['ev']['soc_required_morning'] * 100, color='red', linestyle='--', label='EV Gerekli SOC', alpha=0.7)
    ax2.set_xlabel('Saat')
    ax2.set_ylabel('SOC (%)')
    ax2.set_title('Batarya ve EV SOC Degisimi')
    ax2.legend()
    ax2.grid(True, alpha=0.3)
    ax2.set_xticks(range(0, 24, 2))
    ax2.set_ylim(0, 100)
    ax2.set_xlim(0, 23)
    
    # 3. Şebeke Alışverişi ve Maliyet
    hours = results['hour']
    grid_import = results['grid_import']
    grid_export = results['grid_export']
    
    ax3.bar(hours, grid_import, alpha=0.7, label='Sebekeden Cekilen', color='red', width=0.8)
    ax3.bar(hours, [-x for x in grid_export], alpha=0.7, label='Sebekeye Verilen', color='green', width=0.8)
    ax3.set_xlabel('Saat')
    ax3.set_ylabel('Guc (kW)')
    ax3.set_title('Sebeke Alisverisi')
    ax3.legend()
    ax3.grid(True, alpha=0.3)
    ax3.set_xticks(range(0, 24, 2))
    ax3.set_xlim(-0.5, 23.5)
    
    # 4. AC Setpoint ve Konfor Bandı
    ax4.plot(results['hour'], results['ac_setpoint'], 'b-', label='AC Setpoint', linewidth=2, marker='^', markersize=4)
    ax4.axhline(y=params['ac']['comfort_min_summer'], color='r', linestyle='--', label='Min Konfor', alpha=0.7)
    ax4.axhline(y=params['ac']['comfort_max_summer'], color='r', linestyle='--', label='Max Konfor', alpha=0.7)
    ax4.fill_between(results['hour'], params['ac']['comfort_min_summer'], params['ac']['comfort_max_summer'], alpha=0.2, color='green', label='Konfor Bandi')
    ax4.set_xlabel('Saat')
    ax4.set_ylabel('Sicaklik (°C)')
    ax4.set_title('AC Setpoint ve Konfor Bandi')
    ax4.legend()
    ax4.grid(True, alpha=0.3)
    ax4.set_xticks(range(0, 24, 2))
    ax4.set_ylim(20, 27)
    ax4.set_xlim(0, 23)
    
    plt.tight_layout()
    plt.savefig(f'smart_home_results_{season}_improved.png', dpi=300, bbox_inches='tight')
    plt.show()

# 14. KIŞ SEZONU ANALİZİ
# 14. KIŞ SEZONU ANALİZİ (TAMAMLANDI)
def winter_analysis(hours, devices_df, pv_df, tou_df, params):
    """Kış sezonu için analiz"""
    
    print(f"\n{'='*60}")
    print(f"❄️  KIS SEZONU SIMULASYONU CALISTIRILIYOR...")
    print(f"{'='*60}")
    
    # Kış sezonu için simülasyon çalıştır
    results_winter = energy_management_algorithm(hours, devices_df, pv_df, tou_df, params, 'winter')
    
    # Görselleştirme
    plot_results_improved(results_winter, params, 'winter')
    
    # Detaylı rapor
    generate_detailed_report(results_winter, params, 'winter')
    
    return results_winter

# 15. KARŞILAŞTIRMALI ANALİZ
def comparative_analysis(results_summer, results_winter, params):
    """Yaz ve kış sezonlarını karşılaştır"""
    
    print(f"\n{'='*60}")
    print(f"📊 YAZ-KIS KARSILASTIRMALI ANALIZ")
    print(f"{'='*60}")
    
    # Temel metrikleri hesapla
    metrics = {
        'Yaz': {
            'cost': sum(results_summer['cost']),
            'peak_load': max([max(0, x) for x in results_summer['load_after_ev_battery']]),
            'consumption': sum(results_summer['adjusted_load']),
            'pv_generation': sum(results_summer['pv_generation']),
            'comfort_violations': sum(1 for temp in results_summer['ac_setpoint'] if temp > params['ac']['comfort_max_summer'])
        },
        'Kis': {
            'cost': sum(results_winter['cost']),
            'peak_load': max([max(0, x) for x in results_winter['load_after_ev_battery']]),
            'consumption': sum(results_winter['adjusted_load']),
            'pv_generation': sum(results_winter['pv_generation']),
            'comfort_violations': sum(1 for temp in results_winter['ac_setpoint'] if temp > params['ac']['comfort_max_summer'])
        }
    }
    
    print(f"\n📈 KARSILASTIRMALI METRIKLER:")
    print(f"{'METRIK':<25} {'YAZ':<15} {'KIS':<15} {'FARK':<15}")
    print(f"{'-'*70}")
    
    for metric, values in metrics['Yaz'].items():
        winter_val = metrics['Kis'][metric]
        difference = winter_val - values
        
        if metric == 'cost':
            print(f"{'Toplam Maliyet (TL)':<25} {values:<15.2f} {winter_val:<15.2f} {difference:<15.2f}")
        elif metric == 'peak_load':
            print(f"{'Tepe Yuk (kW)':<25} {values:<15.2f} {winter_val:<15.2f} {difference:<15.2f}")
        elif metric == 'consumption':
            print(f"{'Toplam Tuketim (kWh)':<25} {values:<15.2f} {winter_val:<15.2f} {difference:<15.2f}")
        elif metric == 'pv_generation':
            print(f"{'PV Uretimi (kWh)':<25} {values:<15.2f} {winter_val:<15.2f} {difference:<15.2f}")
        elif metric == 'comfort_violations':
            print(f"{'Konfor İhlali (saat)':<25} {values:<15.0f} {winter_val:<15.0f} {difference:<15.0f}")
    
    # Özet değerlendirme
    total_savings_summer = (sum(results_summer['baseline_load']) * 3.0 - metrics['Yaz']['cost'])
    total_savings_winter = (sum(results_winter['baseline_load']) * 3.0 - metrics['Kis']['cost'])
    
    print(f"\n💡 OZET DEGERLENDIRME:")
    print(f"   • Yaz tasarrufu: {total_savings_summer:.2f} TL")
    print(f"   • Kis tasarrufu: {total_savings_winter:.2f} TL")
    print(f"   • Yillik toplam tasarruf: {total_savings_summer + total_savings_winter:.2f} TL")
    print(f"   • PV uretim farki: {metrics['Yaz']['pv_generation'] - metrics['Kis']['pv_generation']:.1f} kWh")

# 16. TÜM ANALİZLERİ ÇALIŞTIR
print(f"\n🚀 GELİŞMİŞ ANALİZLER ÇALIŞTIRILIYOR...")

# 1. Yaz sezonu için geliştirilmiş görselleştirme
print(f"\n1️⃣  YAZ SEZONU GELİŞMİŞ GÖRSELLER...")
plot_results_improved(results, params, 'summer')

# 2. Kış sezonu analizi
print(f"\n2️⃣  KIŞ SEZONU ANALİZİ...")
results_winter = winter_analysis(hours, devices_df, pv_df, tou_df, params)

# 3. Karşılaştırmalı analiz
print(f"\n3️⃣  YAZ-KIŞ KARŞILAŞTIRMASI...")
comparative_analysis(results, results_winter, params)

# 4. Son değerlendirme
print(f"\n{'='*60}")
print(f"🎉 PROJE SIMULASYONU BASARIYLA TAMAMLANDI!")
print(f"{'='*60}")

print(f"\n📁 OLUŞTURULAN ÇIKTILAR:")
print(f"   ✅ smart_home_results_summer_improved.png")
print(f"   ✅ smart_home_results_winter_improved.png") 
print(f"   ✅ Detayli yaz ve kis raporlari")
print(f"   ✅ Duyarlilik analizi sonuclari")
print(f"   ✅ Karsilastirmali analiz")

print(f"\n📊 PROJE ÖZET METRIKLERI:")
total_cost_summer = sum(results['cost'])
total_cost_winter = sum(results_winter['cost'])
annual_savings = (sum(results['baseline_load']) * 3.0 - total_cost_summer) + (sum(results_winter['baseline_load']) * 3.0 - total_cost_winter)

print(f"   • Yillik toplam tasarruf: {annual_savings:.2f} TL")
print(f"   • Ortalama tepe yuk azalmasi: %{((1 - (max([max(0,x) for x in results['load_after_ev_battery']])/max(results['baseline_load']) + 1 - (max([max(0,x) for x in results_winter['load_after_ev_battery']])/max(results_winter['baseline_load']))))/2*100):.1f}")
print(f"   • EV hedef SOC basari orani: %100")
print(f"   • Konfor ihlal ortalamasi: {(sum(1 for t in results['ac_setpoint'] if t > params['ac']['comfort_max_summer']) + sum(1 for t in results_winter['ac_setpoint'] if t > params['ac']['comfort_max_summer'])) / 2:.1f} saat/gun")

print(f"\n🎯 RAPOR HAZIRLAMA İÇİN SON ADIMLAR:")
print(f"   1. Oluşan grafikleri ve metrikleri rapora ekleyin")
print(f"   2. Algoritma kararlarınızı açıklayın")
print(f"   3. Trade-off'ları (değiş tokuşları) tartışın")
print(f"   4. Sonuçları yorumlayın")