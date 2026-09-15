"""
Calendar Sources & Curated Event Definitions for Pita Media.
Provides verified national days, international days, seasonal milestones, and custom brand events.
"""

from typing import List, Dict, Any, Optional
from core.calendar.event_models import EventDefinition


VERIFIED_EVENT_CATALOG: List[EventDefinition] = [
    # 1. Official National Days & Milestones
    EventDefinition(
        event_id="id_kemerdekaan_17_aug",
        event_name="Hari Kemerdekaan Republik Indonesia (HUT RI)",
        event_type="OFFICIAL_NATIONAL",
        start_date="08-17",
        country="ID",
        audience_scope="Seluruh Masyarakat Indonesia",
        sensitivity_level="LOW",
        default_relevance=95.0,
        source="OFFICIAL_CALENDAR",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_MINI", "PITA_CERITA", "PITA_TRANSFORMASI"],
        tone="Patriotik, Inspiratif, Penuh Semangat Persatuan & Rasa Syukur",
        visual_context="Nuansa Merah Putih elegan, kemeriahan warga nusantara, kehangatan tradisi gotong royong.",
        avoid_guidelines=["Hindari perdebatan politik praktis", "Hindari klaim sejarah yang belum terverifikasi"],
        lead_days=3,
        notes="Momen puncak nasionalisme tahunan."
    ),
    EventDefinition(
        event_id="id_pahlawan_10_nov",
        event_name="Hari Pahlawan Nasional",
        event_type="OFFICIAL_NATIONAL",
        start_date="11-10",
        country="ID",
        audience_scope="Masyarakat Indonesia",
        sensitivity_level="LOW",
        default_relevance=90.0,
        source="OFFICIAL_CALENDAR",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_CERITA", "PITA_MINI"],
        tone="Khidmat, Penuh Penghargaan, Mengangkat Kisah Keteladanan & Pengorbanan",
        visual_context="Tone vintage warm, siluet pejuang atau pahlawan masa kini di kehidupan nyata.",
        avoid_guidelines=["Hindari glorifikasi kekerasan berlebihan"],
        lead_days=2
    ),
    EventDefinition(
        event_id="id_kartini_21_apr",
        event_name="Hari Kartini (Emansipasi & Keteladanan Wanita)",
        event_type="OFFICIAL_NATIONAL",
        start_date="04-21",
        country="ID",
        audience_scope="Masyarakat Indonesia",
        sensitivity_level="LOW",
        default_relevance=88.0,
        source="OFFICIAL_CALENDAR",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_TRANSFORMASI", "PITA_CERITA"],
        tone="Inspiratif, Memberdayakan, Apresiasi Perjuangan Perempuan",
        visual_context="Kain batik, kebaya anggun, potret perempuan tangguh, perpaduan tradisi dan modernitas.",
        avoid_guidelines=["Hindari stereotip sempit"],
        lead_days=2
    ),
    EventDefinition(
        event_id="id_sumpah_pemuda_28_oct",
        event_name="Hari Sumpah Pemuda",
        event_type="OFFICIAL_NATIONAL",
        start_date="10-28",
        country="ID",
        audience_scope="Generasi Muda & Publik Nasional",
        sensitivity_level="LOW",
        default_relevance=90.0,
        source="OFFICIAL_CALENDAR",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_MINI", "PITA_TRANSFORMASI"],
        tone="Enerjik, Visioner, Menggugah Semangat Karya Anak Muda",
        visual_context="Anak muda berkarya, kreativitas nusantara, atmosfer dinamis dan optimis.",
        avoid_guidelines=["Hindari retorika kosong"],
        lead_days=2
    ),
    EventDefinition(
        event_id="id_kesaktian_pancasila_01_oct",
        event_name="Hari Kesaktian Pancasila",
        event_type="OFFICIAL_NATIONAL",
        start_date="10-01",
        country="ID",
        audience_scope="Masyarakat Indonesia",
        sensitivity_level="MEDIUM",
        default_relevance=82.0,
        source="OFFICIAL_CALENDAR",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_MINI", "PITA_CERITA"],
        tone="Khidmat, Menjunjung Tinggi Nilai Luhur Pancasila & Kerukunan",
        visual_context="Garuda Pancasila, keberagaman suku bangsa dalam harmoni.",
        avoid_guidelines=["Hindari polemik politis atau narasi perpecahan"],
        lead_days=1
    ),
    EventDefinition(
        event_id="id_lahir_pancasila_01_jun",
        event_name="Hari Lahir Pancasila",
        event_type="OFFICIAL_NATIONAL",
        start_date="06-01",
        country="ID",
        audience_scope="Masyarakat Indonesia",
        sensitivity_level="LOW",
        default_relevance=85.0,
        source="OFFICIAL_CALENDAR",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_MINI", "PITA_CERITA"],
        tone="Bangga, Edukatif, Harmonis",
        visual_context="Simbol lima sila, kebersamaan lintas budaya nusantara.",
        lead_days=2
    ),
    EventDefinition(
        event_id="id_pendidikan_nasional_02_may",
        event_name="Hari Pendidikan Nasional (Hardiknas)",
        event_type="OFFICIAL_NATIONAL",
        start_date="05-02",
        country="ID",
        audience_scope="Pelajar, Pendidik & Umum",
        sensitivity_level="LOW",
        default_relevance=86.0,
        source="OFFICIAL_CALENDAR",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_TRANSFORMASI", "PITA_CERITA"],
        tone="Edukatif, Membuka Wawasan, Apresiasi Guru & Semangat Belajar",
        visual_context="Buku, ruang kelas penuh harapan, guru yang tulus membimbing murid.",
        lead_days=2
    ),
    EventDefinition(
        event_id="id_kebangkitan_nasional_20_may",
        event_name="Hari Kebangkitan Nasional (Harkitnas)",
        event_type="OFFICIAL_NATIONAL",
        start_date="05-20",
        country="ID",
        audience_scope="Publik Indonesia",
        sensitivity_level="LOW",
        default_relevance=84.0,
        source="OFFICIAL_CALENDAR",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_MINI", "PITA_TRANSFORMASI"],
        tone="Bangkit, Optimis, Inovasi Kemajuan Bangsa",
        visual_context="Langkah maju, teknologi dan tradisi, semangat kebangkitan bersama.",
        lead_days=2
    ),

    # 2. Curated Cultural & Awareness Days
    EventDefinition(
        event_id="id_hari_ibu_22_dec",
        event_name="Hari Ibu Nasional",
        event_type="CURATED_CULTURE",
        start_date="12-22",
        country="ID",
        audience_scope="Keluarga & Masyarakat Umum",
        sensitivity_level="LOW",
        default_relevance=92.0,
        source="CURATED",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_CERITA", "PITA_MINI"],
        tone="Menyentuh Hati, Penuh Kasih, Ungkapan Terima Kasih Mendalam",
        visual_context="Kehangatan pelukan ibu, senyuman penuh kasih, momen sederhana di rumah.",
        avoid_guidelines=["Hindari komersialisasi berlebihan"],
        lead_days=3
    ),
    EventDefinition(
        event_id="id_hari_batik_02_oct",
        event_name="Hari Batik Nasional",
        event_type="CURATED_CULTURE",
        start_date="10-02",
        country="ID",
        audience_scope="Masyarakat Indonesia & Global",
        sensitivity_level="LOW",
        default_relevance=88.0,
        source="CURATED",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_KREASI", "PITA_TRANSFORMASI"],
        tone="Estetis, Apresiasi Mahakarya Warisan Budaya UNESCO",
        visual_context="Detail motif batik canting lilin, kain melambai anggun, warna sogan & indigo alami.",
        lead_days=2
    ),
    EventDefinition(
        event_id="id_hari_guru_25_nov",
        event_name="Hari Guru Nasional",
        event_type="CURATED_CULTURE",
        start_date="11-25",
        country="ID",
        audience_scope="Pelajar, Alumni & Masyarakat",
        sensitivity_level="LOW",
        default_relevance=87.0,
        source="CURATED",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_CERITA", "PITA_MINI"],
        tone="Penuh Rasa Syukur, Penghormatan Pahlawan Tanpa Tanda Jasa",
        visual_context="Senyum tulus guru di papan tulis, kapur, buku catatan, dedikasi di pelosok negeri.",
        lead_days=2
    ),
    EventDefinition(
        event_id="id_hari_anak_23_jul",
        event_name="Hari Anak Nasional",
        event_type="CURATED_CULTURE",
        start_date="07-23",
        country="ID",
        audience_scope="Anak, Orang Tua & Komunitas",
        sensitivity_level="LOW",
        default_relevance=85.0,
        source="CURATED",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_KREASI", "PITA_CERITA"],
        tone="Ceria, Hangat, Perlindungan & Hak Tumbuh Kembang Anak",
        visual_context="Tawa ceria anak-anak bermain di alam bebas, warna-warni cerah yang ramah.",
        lead_days=2
    ),
    EventDefinition(
        event_id="id_lingkungan_hidup_05_jun",
        event_name="Hari Lingkungan Hidup Sedunia",
        event_type="AWARENESS",
        start_date="06-05",
        country="GLOBAL",
        audience_scope="Masyarakat Umum",
        sensitivity_level="LOW",
        default_relevance=86.0,
        source="CURATED",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_KREASI", "PITA_TRANSFORMASI"],
        tone="Peduli, Menyejukkan, Ajakan Nyata Menjaga Bumi",
        visual_context="Hutan hijau asri nusantara, tetesan embun, aksi tanam pohon, laut biru bersih.",
        lead_days=2
    ),
    EventDefinition(
        event_id="id_hari_buku_23_apr",
        event_name="Hari Buku & Hak Cipta Sedunia",
        event_type="AWARENESS",
        start_date="04-23",
        country="GLOBAL",
        audience_scope="Pecinta Literasi & Umum",
        sensitivity_level="LOW",
        default_relevance=83.0,
        source="CURATED",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_CERITA", "PITA_MINI"],
        tone="Literatif, Menyenangkan, Menginspirasi Minat Baca",
        visual_context="Halaman buku terbuka, secangkir kopi hangat, perpustakaan estetik.",
        lead_days=2
    ),

    # 3. Major Respectful Faith & Seasonal Milestones
    EventDefinition(
        event_id="id_tahun_baru_01_jan",
        event_name="Tahun Baru Masehi (Refleksi & Resolusi Awal Tahun)",
        event_type="SEASONAL",
        start_date="01-01",
        country="GLOBAL",
        audience_scope="Seluruh Audiens",
        sensitivity_level="LOW",
        default_relevance=92.0,
        source="OFFICIAL_CALENDAR",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_TRANSFORMASI", "PITA_MINI"],
        tone="Reflektif, Penuh Harapan Baru, Transformasi Diri Lebih Baik",
        visual_context="Cahaya fajar pertama, jurnal resolusi, suasana awal tahun yang tenang dan penuh semangat.",
        lead_days=3
    ),
    EventDefinition(
        event_id="id_ramadan_season",
        event_name="Bulan Suci Ramadan (Bulan Kebaikan & Kebersamaan)",
        event_type="SEASONAL",
        start_date="03-10",  # Seasonal baseline anchor
        end_date="04-09",
        country="ID",
        audience_scope="Masyarakat Indonesia",
        sensitivity_level="MEDIUM",
        default_relevance=94.0,
        source="OFFICIAL_CALENDAR",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_CERITA", "PITA_MINI", "PITA_TRANSFORMASI"],
        tone="Teduh, Penuh Berkah, Menjaga Kebersihan Hati & Berbagi Kebaikan",
        visual_context="Suasana buka puasa bersama keluarga, lentera hangat, masjid indah saat senja.",
        avoid_guidelines=["Jaga netralitas dan rasa saling menghormati antar umat beragama", "Hindari komersialisasi berlebihan"],
        lead_days=3
    ),
    EventDefinition(
        event_id="id_idul_fitri",
        event_name="Hari Raya Idul Fitri (Kemenangan & Silaturahmi)",
        event_type="OFFICIAL_NATIONAL",
        start_date="03-31",  # Approximate anchor
        country="ID",
        audience_scope="Masyarakat Indonesia",
        sensitivity_level="MEDIUM",
        default_relevance=96.0,
        source="OFFICIAL_CALENDAR",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_CERITA", "PITA_TRANSFORMASI"],
        tone="Penuh Kehangatan, Maaf Memaafkan, Kerinduan Kampung Halaman (Mudik)",
        visual_context="Ketupat lebaran, salaman saling memaafkan, suasana hangat kumpul keluarga besar di kampung.",
        avoid_guidelines=["Hindari pamer kemewahan yang menyinggung", "Fokus pada nilai kebersamaan dan maaf-memaafkan"],
        lead_days=4
    ),
    EventDefinition(
        event_id="id_hari_natal_25_dec",
        event_name="Hari Raya Natal (Damai & Kasih Persaudaraan)",
        event_type="OFFICIAL_NATIONAL",
        start_date="12-25",
        country="ID",
        audience_scope="Masyarakat Indonesia & Global",
        sensitivity_level="MEDIUM",
        default_relevance=90.0,
        source="OFFICIAL_CALENDAR",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_CERITA", "PITA_MINI"],
        tone="Damai, Penuh Kasih, Hangat, Menghormati Keberagaman",
        visual_context="Pohon natal bercahaya hangat, suasana damai musim dingin atau kebersamaan keluarga.",
        avoid_guidelines=["Junjung tinggi kerukunan dan saling menghormati"],
        lead_days=3
    ),
    EventDefinition(
        event_id="id_imlek_season",
        event_name="Tahun Baru Imlek (Harmoni & Harapan Kemakmuran)",
        event_type="OFFICIAL_NATIONAL",
        start_date="02-17",
        country="ID",
        audience_scope="Masyarakat Indonesia",
        sensitivity_level="LOW",
        default_relevance=88.0,
        source="OFFICIAL_CALENDAR",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_KREASI", "PITA_CERITA"],
        tone="Ceria, Penuh Harapan Baik, Menghargai Tradisi Leluhur",
        visual_context="Lampion merah anggun, ornamen oriental bernuansa emas, kehangatan makan malam bersama.",
        lead_days=2
    ),

    # 4. Custom Brand Milestones
    EventDefinition(
        event_id="id_pita_media_anniversary_01_aug",
        event_name="Pita Media Anniversary Milestone",
        event_type="CUSTOM_BRAND",
        start_date="08-01",
        country="ID",
        audience_scope="Komunitas Pita Media",
        sensitivity_level="LOW",
        default_relevance=90.0,
        source="CUSTOM",
        verification_status="VERIFIED",
        suggested_pillars=["PITA_TRANSFORMASI", "PITA_CERITA"],
        tone="Apresiasi, Refleksi Perjalanan, Dedikasi Menyajikan Narasi Bermakna",
        visual_context="Visual kilas balik pita waktu, warna brand khas Pita Media, elegansi tipografi.",
        lead_days=3
    ),
]


class CalendarSourceRegistry:
    """Manages event definitions from verified sources, curated catalog, and database custom events."""

    def __init__(self):
        self._events: Dict[str, EventDefinition] = {e.event_id: e for e in VERIFIED_EVENT_CATALOG}

    def list_all_events(self) -> List[EventDefinition]:
        return list(self._events.values())

    def get_event(self, event_id: str) -> Optional[EventDefinition]:
        return self._events.get(event_id)

    def register_custom_event(self, event: EventDefinition) -> None:
        self._events[event.event_id] = event

    def get_events_for_month_day(self, month: int, day: int) -> List[EventDefinition]:
        date_suffix = f"{month:02d}-{day:02d}"
        matches = []
        for e in self._events.values():
            if not e.enabled:
                continue
            if e.start_date.endswith(date_suffix):
                matches.append(e)
        return matches



calendar_sources = CalendarSourceRegistry()


def get_verified_indonesian_events() -> List[EventDefinition]:
    return [e for e in VERIFIED_EVENT_CATALOG if "NATIONAL" in e.event_type or "OFFICIAL" in e.source]


def get_curated_awareness_events() -> List[EventDefinition]:
    return [e for e in VERIFIED_EVENT_CATALOG if "CULTURE" in e.event_type or "AWARENESS" in e.event_type or "GLOBAL" in e.event_type]

