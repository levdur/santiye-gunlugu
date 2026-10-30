# PythonAnywhere "Web" sekmesindeki WSGI dosyasının içeriği bu olmalı.
# KULLANICI_ADIN yazan yeri kendi PythonAnywhere kullanıcı adınla değiştir.
import os
import sys

proje = "/home/KULLANICI_ADIN/santiye-gunlugu"
if proje not in sys.path:
    sys.path.insert(0, proje)

os.environ["GUVENLI_CEREZ"] = "1"          # site HTTPS üzerinden açıldığı için
os.environ["SAAT_DILIMI"] = "Europe/Istanbul"
os.environ["PROJE_ADI"] = "Şantiye Günlüğü"     # üst bantta görünen proje adı

from app import app as application  # noqa: E402
