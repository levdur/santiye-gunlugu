# PythonAnywhere'de yayınlama

Her şey tarayıcıdan yapılır; bilgisayarına bir şey kurman gerekmez. Yaklaşık 30–45 dakika sürer. Aşağıda `KULLANICI_ADIN` gördüğün her yere PythonAnywhere kullanıcı adını yaz.

## 1. Hesap aç
pythonanywhere.com → **Pricing & signup** → **Create a Beginner account**. Seçtiğin kullanıcı adı sitenin adresi olur: `KULLANICI_ADIN.pythonanywhere.com`

## 2. Kodu al
Üst menüden **Consoles** → **Bash**. Açılan siyah ekrana sırayla yaz:

```bash
git clone https://github.com/levdur/santiye-gunlugu.git
cd santiye-gunlugu
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 --version
```

Son satırın gösterdiği sürümü (ör. `Python 3.13.x`) not et.

## 3. Yönetici hesabını oluştur
Aynı ekranda:

```bash
flask --app app kullanici-ekle --yonetici
```

Kullanıcı adı, ad soyad ve şifreyi sorar. Şifreyi iki kez ister (ikincisinde "Repeat for confirmation" yazar). Şifre yazılırken ekranda görünmez; bu normal.

## 4. Web uygulamasını kur
Üst menüden **Web** → **Add a new web app** → **Next** → **Manual configuration** → 2. adımda not ettiğin Python sürümünü seç → **Next**.

Açılan sayfada:

| Bölüm | Ne yazılacak |
|---|---|
| **Virtualenv** | `/home/KULLANICI_ADIN/santiye-gunlugu/.venv` |
| **Static files** → URL | `/static/` |
| **Static files** → Directory | `/home/KULLANICI_ADIN/santiye-gunlugu/static` |
| **Security** → Force HTTPS | Açık |

**Code** bölümündeki **WSGI configuration file** bağlantısına tıkla. İçindeki her şeyi sil, projedeki `wsgi_pythonanywhere.py` dosyasının içeriğini yapıştır, `KULLANICI_ADIN` kısmını düzelt, **Save**.

## 5. Başlat
Web sayfasının üstündeki yeşil **Reload** düğmesine bas. `https://KULLANICI_ADIN.pythonanywhere.com` adresini aç ve giriş yap.

## 6. Bütçe kalemlerini yükle
Sitede **Bütçe kalemleri** → **Şablonu indir**. Şablonu kendi bütçe kodlarınla doldur (ya da mevcut bütçe Excel'ini doğrudan dene; başlıklar tanınırsa olduğu gibi okunur) ve **İçe aktar**. Saha personeli ancak bundan sonra imalat girebilir.

## 7. Kullanıcıları aç
**Kullanıcılar** → **Yeni kullanıcı**. Sahadakiler için rol **Saha personeli**, bütçe ve planlama ekibi için **Ofis**. Kullanıcı adını ve şifreyi kişiye ilet; ilk girişten sonra **Şifre değiştir** sayfasından kendi şifrelerini belirleyebilirler.

## Bakım

| Ne zaman | Ne yapılır |
|---|---|
| Ayda bir (ücretsiz planda) | Web sekmesinde **Run until 1 month from today** düğmesine bas; basmazsan site durur |
| Haftada bir | **Files** → `santiye-gunlugu/instance/` → `santiye.db` dosyasını indir (yedek) |
| Kod güncellenince | Bash konsolunda `cd santiye-gunlugu && git pull`, sonra Web sekmesinde **Reload** |
| Hata olursa | Web sekmesindeki **Error log** bağlantısına bak |

## Bilmen gerekenler

- Ücretsiz planda 512 MB yer var. Küçültülmüş bir fotoğraf ortalama 200–300 KB; günde 5 fotoğrafla yaklaşık bir yıl yeter. Kullanım için **Files** sayfasının altına bak.
- `instance/` klasörü (veritabanı, fotoğraflar, gizli anahtar) GitHub'a gönderilmez; bu bilerek böyle.
