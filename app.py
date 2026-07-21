from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager
from werkzeug.security import generate_password_hash
from sqlalchemy import text
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'sparepart-service-secret-key-2024'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sparepart.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'
login_manager.login_message = 'Silakan login terlebih dahulu.'

# Models
class Cabang(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nama = db.Column(db.String(100), nullable=False)
    alamat = db.Column(db.String(200))
    telepon = db.Column(db.String(20))
    aktif = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(200), nullable=False)
    nama_lengkap = db.Column(db.String(100), nullable=False)
    role = db.Column(db.String(20), nullable=False)  # admin, kasir, teknisi
    cabang_id = db.Column(db.Integer, db.ForeignKey('cabang.id'), nullable=True)
    aktif = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    cabang = db.relationship('Cabang', backref='users')
    
    @property
    def is_active(self):
        return self.aktif
    
    @property
    def is_authenticated(self):
        return True
    
    @property
    def is_anonymous(self):
        return False
    
    def get_id(self):
        return str(self.id)

# --- MODEL BARU: PELANGGAN ---
class Pelanggan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nama = db.Column(db.String(100), nullable=False)
    no_hp = db.Column(db.String(20))
    alamat = db.Column(db.Text)
    cabang_id = db.Column(db.Integer, db.ForeignKey('cabang.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    cabang = db.relationship('Cabang', backref='pelanggans')

class Sparepart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    kode = db.Column(db.String(50), nullable=False)
    nama = db.Column(db.String(100), nullable=False)
    harga_beli = db.Column(db.Float, nullable=False, default=0)
    harga_jual = db.Column(db.Float, nullable=False, default=0)
    harga_jual_2 = db.Column(db.Float, nullable=False, default=0)  # Harga Jual Level 2
    harga_jual_3 = db.Column(db.Float, nullable=False, default=0)  # Harga Jual Level 3
    stok = db.Column(db.Integer, nullable=False, default=0)
    cabang_id = db.Column(db.Integer, db.ForeignKey('cabang.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    cabang = db.relationship('Cabang', backref='spareparts')

class JasaService(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nama = db.Column(db.String(100), nullable=False)
    harga = db.Column(db.Float, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Penjualan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    no_nota = db.Column(db.String(50), nullable=False)
    tanggal = db.Column(db.DateTime, default=datetime.utcnow)
    pelanggan = db.Column(db.String(100))
    total = db.Column(db.Float, default=0)
    diskon = db.Column(db.Float, default=0)
    laba = db.Column(db.Float, default=0)
    
    # Tambahan untuk Kasbon/Piutang
    status_pembayaran = db.Column(db.String(20), default='lunas') # lunas / kasbon
    dibayar = db.Column(db.Float, default=0)
    sisa = db.Column(db.Float, default=0)
    
    cabang_id = db.Column(db.Integer, db.ForeignKey('cabang.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='selesai')
    
    cabang = db.relationship('Cabang', backref='penjualans')
    user = db.relationship('User', backref='penjualans')
    items = db.relationship('PenjualanItem', backref='penjualan', cascade='all, delete-orphan')
    pembayaran_history = db.relationship('PembayaranPiutang', backref='penjualan', cascade='all, delete-orphan')

# MODEL BARU UNTUK HISTORY CICILAN
class PembayaranPiutang(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    penjualan_id = db.Column(db.Integer, db.ForeignKey('penjualan.id'), nullable=False)
    tanggal = db.Column(db.DateTime, default=datetime.utcnow)
    nominal = db.Column(db.Float, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    user = db.relationship('User')

class PenjualanItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    penjualan_id = db.Column(db.Integer, db.ForeignKey('penjualan.id'), nullable=False)
    sparepart_id = db.Column(db.Integer, db.ForeignKey('sparepart.id'), nullable=False)
    jumlah = db.Column(db.Integer, nullable=False)
    harga_beli = db.Column(db.Float, nullable=False)
    harga_jual = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)
    laba = db.Column(db.Float, nullable=False)
    
    sparepart = db.relationship('Sparepart')

class Supplier(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nama = db.Column(db.String(100), nullable=False)
    no_hp = db.Column(db.String(20))
    alamat = db.Column(db.Text)
    cabang_id = db.Column(db.Integer, db.ForeignKey('cabang.id'), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    cabang = db.relationship('Cabang', backref='suppliers')

class Pembelian(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    no_nota = db.Column(db.String(50), nullable=False)
    tanggal = db.Column(db.DateTime, default=datetime.utcnow)
    supplier_id = db.Column(db.Integer, db.ForeignKey('supplier.id'), nullable=True)
    total = db.Column(db.Float, default=0)
    
    status_pembayaran = db.Column(db.String(20), default='lunas') # lunas / belum lunas
    dibayar = db.Column(db.Float, default=0)
    sisa = db.Column(db.Float, default=0)
    
    cabang_id = db.Column(db.Integer, db.ForeignKey('cabang.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(20), default='selesai')
    
    cabang = db.relationship('Cabang', backref='pembelians')
    user = db.relationship('User', backref='pembelians')
    supplier = db.relationship('Supplier', backref='pembelians')
    items = db.relationship('PembelianItem', backref='pembelian', cascade='all, delete-orphan')
    pembayaran_history = db.relationship('PembayaranHutang', backref='pembelian', cascade='all, delete-orphan')

class PembayaranHutang(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pembelian_id = db.Column(db.Integer, db.ForeignKey('pembelian.id'), nullable=False)
    tanggal = db.Column(db.DateTime, default=datetime.utcnow)
    nominal = db.Column(db.Float, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    user = db.relationship('User')

class PembelianItem(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    pembelian_id = db.Column(db.Integer, db.ForeignKey('pembelian.id'), nullable=False)
    sparepart_id = db.Column(db.Integer, db.ForeignKey('sparepart.id'), nullable=False)
    jumlah = db.Column(db.Integer, nullable=False)
    harga_beli = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)
    
    sparepart = db.relationship('Sparepart')

class Service(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    no_nota = db.Column(db.String(50), nullable=False)
    tanggal = db.Column(db.DateTime, default=datetime.utcnow)
    pelanggan = db.Column(db.String(100), nullable=False)
    no_hp = db.Column(db.String(20))
    merk_hp = db.Column(db.String(50))
    kerusakan = db.Column(db.Text)
    teknisi_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    total_jasa = db.Column(db.Float, default=0)
    total_sparepart = db.Column(db.Float, default=0)
    laba_jasa = db.Column(db.Float, default=0)
    laba_sparepart = db.Column(db.Float, default=0)
    total = db.Column(db.Float, default=0)
    cabang_id = db.Column(db.Integer, db.ForeignKey('cabang.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    status = db.Column(db.String(50), default='proses pengerjaan')

    # Tambahan untuk Piutang Service (belum lunas saat diambil)
    status_pembayaran = db.Column(db.String(20), default='lunas')  # lunas / belum lunas
    dibayar = db.Column(db.Float, default=0)
    sisa = db.Column(db.Float, default=0)
    
    cabang = db.relationship('Cabang', backref='services')
    user = db.relationship('User', foreign_keys=[user_id], backref='services_created')
    teknisi = db.relationship('User', foreign_keys=[teknisi_id], backref='services_handled')
    items_sparepart = db.relationship('ServiceSparepart', backref='service', cascade='all, delete-orphan')
    items_jasa = db.relationship('ServiceJasa', backref='service', cascade='all, delete-orphan')
    pembayaran_history = db.relationship('PembayaranServicePiutang', backref='service', cascade='all, delete-orphan')

# MODEL BARU UNTUK HISTORY CICILAN SERVICE
class PembayaranServicePiutang(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(db.Integer, db.ForeignKey('service.id'), nullable=False)
    tanggal = db.Column(db.DateTime, default=datetime.utcnow)
    nominal = db.Column(db.Float, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

    user = db.relationship('User')

class ServiceSparepart(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(db.Integer, db.ForeignKey('service.id'), nullable=False)
    sparepart_id = db.Column(db.Integer, db.ForeignKey('sparepart.id'), nullable=False)
    jumlah = db.Column(db.Integer, nullable=False)
    harga_beli = db.Column(db.Float, nullable=False)
    harga_jual = db.Column(db.Float, nullable=False)
    subtotal = db.Column(db.Float, nullable=False)
    laba = db.Column(db.Float, nullable=False)
    
    sparepart = db.relationship('Sparepart')

class ServiceJasa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    service_id = db.Column(db.Integer, db.ForeignKey('service.id'), nullable=False)
    jasa_id = db.Column(db.Integer, db.ForeignKey('jasa_service.id'), nullable=False)
    harga = db.Column(db.Float, nullable=False)
    
    jasa = db.relationship('JasaService')

class Retur(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    no_retur = db.Column(db.String(50), nullable=False)
    tanggal = db.Column(db.DateTime, default=datetime.utcnow)
    tipe = db.Column(db.String(20), nullable=False)  # pelanggan / supplier / service
    referensi_id = db.Column(db.Integer)  # ID nota penjualan atau service
    referensi_tipe = db.Column(db.String(20))  # penjualan / service
    sudah_diretur = db.Column(db.Boolean, default=False)  # Cegah retur berulang
    sparepart_id = db.Column(db.Integer, db.ForeignKey('sparepart.id'))
    jumlah = db.Column(db.Integer, nullable=False)
    nominal = db.Column(db.Float, nullable=False)
    keterangan = db.Column(db.Text)
    cabang_id = db.Column(db.Integer, db.ForeignKey('cabang.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    cabang = db.relationship('Cabang', backref='returs')
    user = db.relationship('User', backref='returs')
    sparepart = db.relationship('Sparepart')

class KasMasuk(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tanggal = db.Column(db.DateTime, default=datetime.utcnow)
    sumber = db.Column(db.String(50), nullable=False)
    referensi_id = db.Column(db.Integer)
    nominal = db.Column(db.Float, nullable=False)
    keterangan = db.Column(db.Text)
    cabang_id = db.Column(db.Integer, db.ForeignKey('cabang.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    cabang = db.relationship('Cabang', backref='kas_masuks')
    user = db.relationship('User', backref='kas_masuks')

class KasKeluar(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    tanggal = db.Column(db.DateTime, default=datetime.utcnow)
    keperluan = db.Column(db.String(100), nullable=False)
    nominal = db.Column(db.Float, nullable=False)
    keterangan = db.Column(db.Text)
    cabang_id = db.Column(db.Integer, db.ForeignKey('cabang.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    cabang = db.relationship('Cabang', backref='kas_keluars')
    user = db.relationship('User', backref='kas_keluars')

class Wallet(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nama = db.Column(db.String(50), nullable=False)
    stok_saldo = db.Column(db.Float, default=0)
    biaya_admin_default = db.Column(db.Float, default=0)
    cabang_id = db.Column(db.Integer, db.ForeignKey('cabang.id'), nullable=False)
    
    cabang = db.relationship('Cabang', backref='wallets')

class TopUp(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    no_nota = db.Column(db.String(50), nullable=False)
    tanggal = db.Column(db.DateTime, default=datetime.utcnow)
    pelanggan = db.Column(db.String(100))
    wallet_id = db.Column(db.Integer, db.ForeignKey('wallet.id'), nullable=False)
    nominal = db.Column(db.Float, nullable=False)
    biaya_admin = db.Column(db.Float, default=0)
    total_bayar = db.Column(db.Float, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    cabang_id = db.Column(db.Integer, db.ForeignKey('cabang.id'), nullable=False)
    
    wallet = db.relationship('Wallet', backref='topups')
    user = db.relationship('User', backref='topups')
    cabang = db.relationship('Cabang', backref='topups_cabang')

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

class Pengaturan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nama_aplikasi = db.Column(db.String(100), default='Sparepart System')
    tagline = db.Column(db.String(100), default='System')
    logo = db.Column(db.String(200), default='logo.png')

@app.context_processor
def inject_pengaturan():
    try:
        setting = Pengaturan.query.first()
    except Exception:
        setting = None
    if not setting:
        setting = Pengaturan(nama_aplikasi='Sparepart System', tagline='System', logo='logo.png')

    try:
        default_cabang = Cabang.query.order_by(Cabang.id.asc()).first()
        default_cabang_id = default_cabang.id if default_cabang else ''
    except Exception:
        default_cabang_id = ''

    return dict(app_setting=setting, default_cabang_id=default_cabang_id)

def init_db():
    with app.app_context():
        db.create_all()

        # --- Migrasi kolom baru (Piutang Service) untuk DB lama yang sudah ada ---
        with db.engine.connect() as conn:
            existing_cols = [row[1] for row in conn.execute(text("PRAGMA table_info(service)"))]
            if 'status_pembayaran' not in existing_cols:
                conn.execute(text("ALTER TABLE service ADD COLUMN status_pembayaran VARCHAR(20) DEFAULT 'lunas'"))
            if 'dibayar' not in existing_cols:
                conn.execute(text("ALTER TABLE service ADD COLUMN dibayar FLOAT DEFAULT 0"))
            if 'sisa' not in existing_cols:
                conn.execute(text("ALTER TABLE service ADD COLUMN sisa FLOAT DEFAULT 0"))
            conn.commit()

        # --- Pastikan selalu ada 1 baris Pengaturan ---
        if not Pengaturan.query.first():
            db.session.add(Pengaturan(nama_aplikasi='Sparepart System', tagline='System', logo='logo.png'))
            db.session.commit()

        # --- Migrasi: Nonaktifkan Multi-Cabang (gabungkan semua data ke 1 cabang tunggal) ---
        cabang_utama = Cabang.query.order_by(Cabang.id.asc()).first()
        if cabang_utama:
            with db.engine.connect() as conn:
                tabel_dengan_cabang = ['user', 'pelanggan', 'sparepart', 'supplier', 'penjualan',
                                        'pembelian', 'service', 'retur', 'kas_masuk', 'kas_keluar',
                                        'wallet', 'top_up']
                for tbl in tabel_dengan_cabang:
                    conn.execute(
                        text(f"UPDATE {tbl} SET cabang_id = :cid WHERE cabang_id IS NOT NULL AND cabang_id != :cid"),
                        {"cid": cabang_utama.id}
                    )
                conn.commit()
            # Nonaktifkan cabang lain supaya tidak muncul lagi di mana pun
            Cabang.query.filter(Cabang.id != cabang_utama.id).update({Cabang.aktif: False})
            db.session.commit()
        
        # Cek apakah sudah ada data
        if not Cabang.query.first():
            # Buat cabang default
            cabang1 = Cabang(nama='Toko Pusat', alamat='Jl. Utama No. 1', telepon='081234567890')
            db.session.add(cabang1)
            db.session.commit()
            
            # Buat admin default
            admin = User(
                username='admin',
                password=generate_password_hash('admin123'),
                nama_lengkap='Administrator',
                role='admin',
                cabang_id=None
            )
            db.session.add(admin)
            
            # Buat jasa service default
            jasa_list =[
                JasaService(nama='Ganti LCD', harga=100000),
                JasaService(nama='Ganti Baterai', harga=50000),
                JasaService(nama='Ganti Konektor Cas', harga=75000),
                JasaService(nama='Software/Flashing', harga=50000),
                JasaService(nama='Ganti Touchscreen', harga=80000),
            ]
            for jasa in jasa_list:
                db.session.add(jasa)
            
            db.session.commit()
            print("Database initialized with default data.")

from routes import *

if __name__ == '__main__':
    init_db()
    # Tambahkan host='0.0.0.0' agar bisa diakses di jaringan lokal (Wi-Fi/LAN)
    # port=5000 bisa Anda ganti sesuai keinginan (opsional)
    app.run(host='0.0.0.0', port=5000, debug=True)