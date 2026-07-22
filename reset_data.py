"""
Script reset data untuk Apps_konter (Flask + SQLAlchemy)
=========================================================

FUNGSI:
Mengosongkan seluruh data transaksi (Penjualan, Pembelian, Service, Retur)
dan master data (Sparepart, Pelanggan, Supplier, JasaService, Cabang, User)
KECUALI 1 akun admin yang dipertahankan.

PENTING - BACA DULU SEBELUM MENJALANKAN:
1. WAJIB BACKUP dulu file database sebelum menjalankan script ini!
   Lokasi database ada di folder instance/, contoh:
       cp instance/sparepart.db instance/sparepart_backup_$(date +%Y%m%d).db

2. Script ini akan tetap membuat 1 Cabang default ("Toko Pusat") dan
   mengarahkan admin ke cabang tersebut. Ini supaya aplikasi tidak error
   ketika kamu mau input Sparepart/Penjualan baru (field cabang_id wajib diisi).

3. Jalankan script ini dari folder root project (folder yang berisi app.py),
   bukan dari folder lain, karena script ini meng-import app.py.

4. Kas Masuk / Kas Keluar / Wallet / TopUp TIDAK dihapus oleh script ini
   karena tidak diminta secara eksplisit. Kalau kamu juga mau mengosongkan
   itu, beri tahu saya, atau tambahkan sendiri di bagian bawah script ini
   mengikuti pola yang sama.

CARA MENJALANKAN:
    python reset_data.py
    (akan ada konfirmasi ketik "YA HAPUS" sebelum eksekusi)
"""

import sys
import os

# Pastikan folder project ada di path supaya "from app import ..." berhasil
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import (
    app, db,
    Cabang, User, Pelanggan, Sparepart, JasaService,
    Penjualan, PenjualanItem, PembayaranPiutang,
    Supplier, Pembelian, PembelianItem, PembayaranHutang,
    Service, ServiceSparepart, ServiceJasa, PembayaranServicePiutang,
    Retur,
)
from werkzeug.security import generate_password_hash


def konfirmasi():
    print("=" * 60)
    print("PERINGATAN: Script ini akan MENGHAPUS PERMANEN data berikut:")
    print("  - Semua Sparepart (master barang + stok)")
    print("  - Semua Penjualan (nota, item, cicilan piutang)")
    print("  - Semua Pembelian (nota ke supplier + hutang)")
    print("  - Semua Service HP (nota servis)")
    print("  - Semua Retur")
    print("  - Semua Pelanggan, Supplier, JasaService")
    print("  - Semua Cabang (kecuali 1 cabang default baru)")
    print("  - Semua User KECUALI admin pertama yang ditemukan")
    print("=" * 60)
    jawaban = input("Sudah backup database? Ketik 'YA HAPUS' untuk lanjut: ")
    return jawaban.strip() == "YA HAPUS"


def reset_data():
    with app.app_context():
        # 1. Hapus data transaksi Penjualan (anak dulu baru induk)
        PembayaranPiutang.query.delete()
        PenjualanItem.query.delete()
        Penjualan.query.delete()

        # 2. Hapus data transaksi Pembelian
        PembayaranHutang.query.delete()
        PembelianItem.query.delete()
        Pembelian.query.delete()

        # 3. Hapus data Service
        PembayaranServicePiutang.query.delete()
        ServiceSparepart.query.delete()
        ServiceJasa.query.delete()
        Service.query.delete()

        # 4. Hapus Retur (referensi ke sparepart, harus sebelum sparepart dihapus)
        Retur.query.delete()

        # 5. Hapus master data barang & jasa
        Sparepart.query.delete()
        JasaService.query.delete()

        # 6. Hapus Pelanggan & Supplier
        Pelanggan.query.delete()
        Supplier.query.delete()

        # 7. Simpan 1 admin, hapus user lainnya
        admin = User.query.filter_by(role='admin').order_by(User.id.asc()).first()
        if not admin:
            # Kalau tidak ada admin sama sekali, buat admin baru
            print("Tidak ditemukan user admin, membuat admin baru (admin/admin123)...")
            admin = User(
                username='admin',
                password=generate_password_hash('admin123'),
                nama_lengkap='Administrator',
                role='admin',
            )
            db.session.add(admin)
            db.session.flush()
        else:
            User.query.filter(User.id != admin.id).delete()

        # 8. Hapus semua Cabang lama, buat 1 Cabang default baru
        Cabang.query.delete()
        db.session.flush()

        cabang_baru = Cabang(nama='Toko Pusat', alamat='', telepon='')
        db.session.add(cabang_baru)
        db.session.flush()

        # Arahkan admin ke cabang baru supaya konsisten
        admin.cabang_id = cabang_baru.id

        db.session.commit()
        print("Selesai. Data sparepart, transaksi, dan master data sudah dikosongkan.")
        print(f"Admin yang dipertahankan: username='{admin.username}'")
        print(f"Cabang default baru: '{cabang_baru.nama}' (id={cabang_baru.id})")


if __name__ == '__main__':
    if konfirmasi():
        reset_data()
    else:
        print("Dibatalkan. Tidak ada data yang dihapus.")
