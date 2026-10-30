CREATE TABLE IF NOT EXISTS kullanici (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kullanici_adi TEXT    NOT NULL UNIQUE,
    ad            TEXT    NOT NULL,
    sifre_hash    TEXT    NOT NULL,
    rol           TEXT    NOT NULL CHECK (rol IN ('yonetici', 'ofis', 'personel')),
    aktif         INTEGER NOT NULL DEFAULT 1,
    hatali_giris  INTEGER NOT NULL DEFAULT 0,
    kilit_bitis   TEXT,
    olusturma     TEXT    NOT NULL
);

-- Ofisin tanımladığı bütçe kalemleri (keşif / maliyet kodları)
CREATE TABLE IF NOT EXISTS butce_kalemi (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kod           TEXT    NOT NULL UNIQUE,
    tanim         TEXT    NOT NULL,
    birim         TEXT    NOT NULL,
    butce_miktar  REAL,
    birim_fiyat   REAL,
    aktivite_kodu TEXT,                -- iş programındaki aktivite (P6 / MS Project / Excel)
    devir_miktar  REAL NOT NULL DEFAULT 0,  -- sistem kullanılmaya başlamadan önce yapılmış kümülatif miktar
    aktif         INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS iscilik_sinifi (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    ad    TEXT    NOT NULL UNIQUE,
    sira  INTEGER NOT NULL DEFAULT 0,
    aktif INTEGER NOT NULL DEFAULT 1
);

-- Günlük rapor başlığı: günde tek kayıt
CREATE TABLE IF NOT EXISTS kayit (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    tarih          TEXT    NOT NULL UNIQUE,
    hava           TEXT    NOT NULL,
    sicaklik       INTEGER,
    makineler      TEXT,
    notlar         TEXT,
    olusturan_id   INTEGER NOT NULL REFERENCES kullanici(id),
    olusturma      TEXT    NOT NULL,
    guncelleyen_id INTEGER REFERENCES kullanici(id),
    guncelleme     TEXT,
    onayli         INTEGER NOT NULL DEFAULT 0,
    onaylayan_id   INTEGER REFERENCES kullanici(id),
    onay_zamani    TEXT
);

-- Yapılan imalat: her satır bir bütçe koduna bağlı
CREATE TABLE IF NOT EXISTS imalat_satiri (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    kayit_id  INTEGER NOT NULL REFERENCES kayit(id) ON DELETE CASCADE,
    kalem_id  INTEGER NOT NULL REFERENCES butce_kalemi(id),
    miktar    REAL    NOT NULL,
    lokasyon  TEXT,
    aciklama  TEXT
);

CREATE TABLE IF NOT EXISTS iscilik_satiri (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    kayit_id  INTEGER NOT NULL REFERENCES kayit(id) ON DELETE CASCADE,
    sinif_id  INTEGER NOT NULL REFERENCES iscilik_sinifi(id),
    kisi      INTEGER NOT NULL,
    UNIQUE (kayit_id, sinif_id)
);

CREATE TABLE IF NOT EXISTS fotograf (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kayit_id    INTEGER NOT NULL REFERENCES kayit(id) ON DELETE CASCADE,
    dosya       TEXT    NOT NULL,
    yukleyen_id INTEGER NOT NULL REFERENCES kullanici(id),
    olusturma   TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_imalat_kayit ON imalat_satiri(kayit_id);
CREATE INDEX IF NOT EXISTS ix_imalat_kalem ON imalat_satiri(kalem_id);
CREATE INDEX IF NOT EXISTS ix_iscilik_kayit ON iscilik_satiri(kayit_id);
