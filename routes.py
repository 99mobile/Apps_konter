from flask import render_template, request, redirect, url_for, flash, jsonify
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from app import TopUp, Wallet, app, db, User, Cabang, Sparepart, JasaService, Penjualan, PenjualanItem, Pelanggan
from app import Service, ServiceSparepart, ServiceJasa, Retur, KasMasuk, KasKeluar
from app import Pengaturan, PembayaranServicePiutang
from datetime import datetime, timedelta
from functools import wraps
from sqlalchemy import func
from app import PembayaranPiutang, Supplier, Pembelian, PembayaranHutang, PembelianItem
import csv
import io
import os
from flask import Response

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if current_user.role != 'admin':
            flash('Akses ditolak. Hanya admin yang bisa mengakses halaman ini.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def get_user_cabang_id():
    # Multi-cabang dinonaktifkan: seluruh data digabung jadi satu, tanpa filter cabang.
    return None

def get_default_cabang_id():
    """Cabang tunggal yang dipakai otomatis untuk semua transaksi (multi-cabang dinonaktifkan)."""
    c = Cabang.query.first()
    return c.id if c else None

def generate_nota(prefix, model, field='no_nota'):
    today = datetime.now().strftime('%Y%m%d')
    column = getattr(model, field)
    last = model.query.filter(column.like(f'{prefix}{today}%')).order_by(model.id.desc()).first()
    if last:
        last_value = getattr(last, field)
        num = int(last_value[-4:]) + 1
    else:
        num = 1
    return f'{prefix}{today}{num:04d}'

# Auth Routes
@app.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password, password):
            if not user.aktif:
                flash('Akun Anda tidak aktif. Hubungi admin.', 'error')
                return redirect(url_for('login'))
            login_user(user)
            flash(f'Selamat datang, {user.nama_lengkap}!', 'success')
            return redirect(url_for('dashboard'))
        flash('Username atau password salah!', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Anda telah logout.', 'info')
    return redirect(url_for('login'))

# Dashboard
@app.route('/dashboard')
@login_required
def dashboard():
    cabang_id = get_user_cabang_id()
    today = datetime.now().date()
    
    # Logic untuk mendapatkan target_cabang_id
    if current_user.role == 'admin':
        cabangs = Cabang.query.filter_by(aktif=True).all()
        target_cabang = cabang_id # Bisa None (Semua Cabang) atau ID tertentu
    else:
        cabangs =[]
        target_cabang = current_user.cabang_id

    # --- QUERY PENJUALAN ---
    q_pj = db.session.query(
        func.count(Penjualan.id),
        func.sum(Penjualan.laba)
    ).filter(func.date(Penjualan.tanggal) == today)

    # --- QUERY SERVICE ---
    q_sv = db.session.query(
        func.count(Service.id),
        func.sum(Service.laba_jasa),
        func.sum(Service.laba_sparepart)
    ).filter(func.date(Service.tanggal) == today)

    # --- QUERY KAS ---
    q_km = db.session.query(func.sum(KasMasuk.nominal)).filter(func.date(KasMasuk.tanggal) == today)
    q_kk = db.session.query(func.sum(KasKeluar.nominal)).filter(func.date(KasKeluar.tanggal) == today)

    # Terapkan Filter Cabang jika ada target_cabang
    if target_cabang:
        q_pj = q_pj.filter(Penjualan.cabang_id == target_cabang)
        q_sv = q_sv.filter(Service.cabang_id == target_cabang)
        q_km = q_km.filter(KasMasuk.cabang_id == target_cabang)
        q_kk = q_kk.filter(KasKeluar.cabang_id == target_cabang)

    # Eksekusi dan Ambil Hasil
    res_pj = q_pj.first()
    penjualan_hari = res_pj[0] or 0
    laba_penjualan = res_pj[1] or 0

    res_sv = q_sv.first()
    service_hari = res_sv[0] or 0
    laba_jasa = res_sv[1] or 0
    laba_sp_service = res_sv[2] or 0

    kas_masuk = q_km.scalar() or 0
    kas_keluar = q_kk.scalar() or 0

    # --- QUERY PIUTANG (Total Uang Belum Lunas Sepanjang Waktu) ---
    q_piutang = db.session.query(func.sum(Penjualan.sisa)).filter(Penjualan.status_pembayaran == 'kasbon', Penjualan.sisa > 0)
    q_piutang_sv = db.session.query(func.sum(Service.sisa)).filter(Service.status_pembayaran == 'belum lunas', Service.sisa > 0)
    # Service yang belum selesai (belum diambil) ikut dihitung sebagai piutang (uang belum diterima)
    q_piutang_sv_proses = db.session.query(func.sum(Service.total)).filter(Service.status.in_(['proses pengerjaan', 'sudah selesai dikerjakan']))
    q_hutang = db.session.query(func.sum(Pembelian.sisa)).filter(Pembelian.status_pembayaran == 'belum lunas', Pembelian.sisa > 0)
    if target_cabang:
        q_piutang = q_piutang.filter(Penjualan.cabang_id == target_cabang)
        q_piutang_sv = q_piutang_sv.filter(Service.cabang_id == target_cabang)
        q_piutang_sv_proses = q_piutang_sv_proses.filter(Service.cabang_id == target_cabang)
        q_hutang = q_hutang.filter(Pembelian.cabang_id == target_cabang)
    total_piutang = (q_piutang.scalar() or 0) + (q_piutang_sv.scalar() or 0) + (q_piutang_sv_proses.scalar() or 0)
    total_hutang = q_hutang.scalar() or 0

    # Kalkulasi Final
    # Total Laba = Laba Produk + Laba Jasa + Laba Sparepart Service
    total_laba = laba_penjualan + laba_jasa + laba_sp_service
    saldo_kas = kas_masuk - kas_keluar

    # --- QUERY 5 PRODUK BEST SELLER (Berdasarkan Total Qty Terjual) ---
    q_best_seller = db.session.query(
        Sparepart.id,
        Sparepart.nama,
        Sparepart.kode,
        func.sum(PenjualanItem.jumlah).label('total_terjual'),
        func.sum(PenjualanItem.subtotal).label('total_omzet')
    ).join(PenjualanItem, PenjualanItem.sparepart_id == Sparepart.id) \
     .join(Penjualan, Penjualan.id == PenjualanItem.penjualan_id)

    if target_cabang:
        q_best_seller = q_best_seller.filter(Penjualan.cabang_id == target_cabang)

    best_sellers = q_best_seller.group_by(Sparepart.id) \
        .order_by(func.sum(PenjualanItem.jumlah).desc()) \
        .limit(5).all()

    return render_template('dashboard.html', 
                         cabangs=cabangs, 
                         cabang_id=cabang_id,
                         penjualan_hari=penjualan_hari, 
                         service_hari=service_hari,
                         kas_masuk=kas_masuk, 
                         kas_keluar=kas_keluar, 
                         total_laba=total_laba, 
                         saldo_kas=saldo_kas,
                         total_piutang=total_piutang,
                         total_hutang=total_hutang,
                         best_sellers=best_sellers)

# Cabang Management
@app.route('/cabang')
@login_required
@admin_required
def cabang():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    query = Cabang.query
    if search:
        query = query.filter(Cabang.nama.ilike(f'%{search}%'))
    pagination = query.paginate(page=page, per_page=10, error_out=False)
    total_cabang = Cabang.query.count()
    return render_template('cabang.html', pagination=pagination, search=search, total_cabang=total_cabang)

@app.route('/cabang/tambah', methods=['POST'])
@login_required
@admin_required
def cabang_tambah():
    if Cabang.query.count() >= 3:
        flash('Maksimal hanya bisa 3 cabang!', 'error')
        return redirect(url_for('cabang'))
    nama = request.form.get('nama')
    alamat = request.form.get('alamat')
    telepon = request.form.get('telepon')
    cabang = Cabang(nama=nama, alamat=alamat, telepon=telepon)
    db.session.add(cabang)
    db.session.commit()
    flash('Cabang berhasil ditambahkan!', 'success')
    return redirect(url_for('cabang'))

@app.route('/cabang/edit/<int:id>', methods=['POST'])
@login_required
@admin_required
def cabang_edit(id):
    cabang = Cabang.query.get_or_404(id)
    cabang.nama = request.form.get('nama')
    cabang.alamat = request.form.get('alamat')
    cabang.telepon = request.form.get('telepon')
    cabang.aktif = request.form.get('aktif') == 'on'
    db.session.commit()
    flash('Cabang berhasil diupdate!', 'success')
    return redirect(url_for('cabang'))

@app.route('/cabang/hapus/<int:id>')
@login_required
@admin_required
def cabang_hapus(id):
    cabang = Cabang.query.get_or_404(id)
    db.session.delete(cabang)
    db.session.commit()
    flash('Cabang berhasil dihapus!', 'success')
    return redirect(url_for('cabang'))

# User Management
ALLOWED_LOGO_EXT = {'png', 'jpg', 'jpeg', 'webp', 'gif'}

def _allowed_logo_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_LOGO_EXT

@app.route('/pengaturan')
@login_required
@admin_required
def pengaturan():
    setting = Pengaturan.query.first()
    if not setting:
        setting = Pengaturan(nama_aplikasi='Sparepart System', tagline='System', logo='logo.png')
        db.session.add(setting)
        db.session.commit()
    return render_template('pengaturan.html', setting=setting)

@app.route('/pengaturan/simpan', methods=['POST'])
@login_required
@admin_required
def pengaturan_simpan():
    setting = Pengaturan.query.first()
    if not setting:
        setting = Pengaturan()
        db.session.add(setting)

    nama_aplikasi = request.form.get('nama_aplikasi', '').strip()
    tagline = request.form.get('tagline', '').strip()

    if nama_aplikasi:
        setting.nama_aplikasi = nama_aplikasi
    if tagline:
        setting.tagline = tagline

    file = request.files.get('logo')
    if file and file.filename:
        if _allowed_logo_file(file.filename):
            ext = file.filename.rsplit('.', 1)[1].lower()
            filename = secure_filename(f'logo_app.{ext}')
            save_dir = os.path.join(app.root_path, 'static', 'images')
            os.makedirs(save_dir, exist_ok=True)
            file.save(os.path.join(save_dir, filename))
            setting.logo = filename
        else:
            flash('Format logo tidak didukung. Gunakan PNG, JPG, JPEG, WEBP, atau GIF.', 'error')
            return redirect(url_for('pengaturan'))

    db.session.commit()
    flash('Pengaturan aplikasi berhasil disimpan!', 'success')
    return redirect(url_for('pengaturan'))

@app.route('/users')
@login_required
@admin_required
def users():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    query = User.query
    if search:
        query = query.filter(User.nama_lengkap.ilike(f'%{search}%') | User.username.ilike(f'%{search}%'))
    pagination = query.paginate(page=page, per_page=10, error_out=False)
    cabangs = Cabang.query.filter_by(aktif=True).all()
    return render_template('users.html', pagination=pagination, search=search, cabangs=cabangs)

@app.route('/users/tambah', methods=['POST'])
@login_required
@admin_required
def users_tambah():
    username = request.form.get('username')
    if User.query.filter_by(username=username).first():
        flash('Username sudah digunakan!', 'error')
        return redirect(url_for('users'))
    password = request.form.get('password')
    nama_lengkap = request.form.get('nama_lengkap')
    role = request.form.get('role')
    cabang_id = get_default_cabang_id()
    user = User(username=username, password=generate_password_hash(password),
                nama_lengkap=nama_lengkap, role=role, cabang_id=cabang_id)
    db.session.add(user)
    db.session.commit()
    flash('User berhasil ditambahkan!', 'success')
    return redirect(url_for('users'))

@app.route('/users/edit/<int:id>', methods=['POST'])
@login_required
@admin_required
def users_edit(id):
    user = User.query.get_or_404(id)
    user.nama_lengkap = request.form.get('nama_lengkap')
    user.role = request.form.get('role')
    user.cabang_id = get_default_cabang_id()
    user.aktif = request.form.get('aktif') == 'on'
    password = request.form.get('password')
    if password:
        user.password = generate_password_hash(password)
    db.session.commit()
    flash('User berhasil diupdate!', 'success')
    return redirect(url_for('users'))

@app.route('/users/hapus/<int:id>')
@login_required
@admin_required
def users_hapus(id):
    user = User.query.get_or_404(id)
    if user.id == current_user.id:
        flash('Tidak bisa menghapus akun sendiri!', 'error')
        return redirect(url_for('users'))
    db.session.delete(user)
    db.session.commit()
    flash('User berhasil dihapus!', 'success')
    return redirect(url_for('users'))

# --- MASTER PELANGGAN ---
@app.route('/pelanggan')
@login_required
def pelanggan():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    cabang_id = get_user_cabang_id()
    
    query = Pelanggan.query
    if current_user.role != 'admin':
        query = query.filter_by(cabang_id=current_user.cabang_id)
    elif cabang_id:
        query = query.filter_by(cabang_id=cabang_id)
    if search:
        query = query.filter(Pelanggan.nama.ilike(f'%{search}%') | Pelanggan.no_hp.ilike(f'%{search}%'))
    
    pagination = query.order_by(Pelanggan.nama.asc()).paginate(page=page, per_page=10, error_out=False)
    cabangs = Cabang.query.filter_by(aktif=True).all() if current_user.role == 'admin' else[]
    return render_template('pelanggan.html', pagination=pagination, search=search, cabangs=cabangs, cabang_id=cabang_id)

@app.route('/pelanggan/tambah', methods=['POST'])
@login_required
def pelanggan_tambah():
    nama = request.form.get('nama')
    no_hp = request.form.get('no_hp')
    alamat = request.form.get('alamat')
    cabang_id = get_default_cabang_id()
    p = Pelanggan(nama=nama, no_hp=no_hp, alamat=alamat, cabang_id=cabang_id)
    db.session.add(p)
    db.session.commit()
    flash('Pelanggan berhasil ditambahkan!', 'success')
    return redirect(url_for('pelanggan'))

@app.route('/pelanggan/edit/<int:id>', methods=['POST'])
@login_required
def pelanggan_edit(id):
    p = Pelanggan.query.get_or_404(id)
    if current_user.role != 'admin' and p.cabang_id != current_user.cabang_id:
        flash('Akses ditolak!', 'error')
        return redirect(url_for('pelanggan'))
        
    p.nama = request.form.get('nama')
    p.no_hp = request.form.get('no_hp')
    p.alamat = request.form.get('alamat')
    db.session.commit()
    flash('Pelanggan berhasil diupdate!', 'success')
    return redirect(url_for('pelanggan'))

@app.route('/pelanggan/hapus/<int:id>')
@login_required
def pelanggan_hapus(id):
    p = Pelanggan.query.get_or_404(id)
    if current_user.role != 'admin' and p.cabang_id != current_user.cabang_id:
        flash('Akses ditolak!', 'error')
        return redirect(url_for('pelanggan'))
    db.session.delete(p)
    db.session.commit()
    flash('Pelanggan berhasil dihapus!', 'success')
    return redirect(url_for('pelanggan'))

@app.route('/api/pelanggan/<int:cabang_id>')
@login_required
def api_pelanggan(cabang_id):
    pelanggans = Pelanggan.query.filter_by(cabang_id=cabang_id).order_by(Pelanggan.nama.asc()).all()
    return jsonify([{'id': p.id, 'nama': p.nama, 'no_hp': p.no_hp} for p in pelanggans])


# --- MASTER SUPPLIER ---
@app.route('/supplier')
@login_required
def supplier():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    cabang_id = get_user_cabang_id()

    query = Supplier.query
    if current_user.role != 'admin':
        query = query.filter_by(cabang_id=current_user.cabang_id)
    elif cabang_id:
        query = query.filter_by(cabang_id=cabang_id)
    if search:
        query = query.filter(Supplier.nama.ilike(f'%{search}%') | Supplier.no_hp.ilike(f'%{search}%'))

    pagination = query.order_by(Supplier.nama.asc()).paginate(page=page, per_page=10, error_out=False)
    cabangs = Cabang.query.filter_by(aktif=True).all() if current_user.role == 'admin' else []
    return render_template('supplier.html', pagination=pagination, search=search, cabangs=cabangs, cabang_id=cabang_id)

@app.route('/supplier/tambah', methods=['POST'])
@login_required
def supplier_tambah():
    nama = request.form.get('nama')
    no_hp = request.form.get('no_hp')
    alamat = request.form.get('alamat')
    cabang_id = get_default_cabang_id()

    s = Supplier(nama=nama, no_hp=no_hp, alamat=alamat, cabang_id=cabang_id)
    db.session.add(s)
    db.session.commit()
    flash('Supplier berhasil ditambahkan!', 'success')
    return redirect(url_for('supplier'))

@app.route('/supplier/edit/<int:id>', methods=['POST'])
@login_required
def supplier_edit(id):
    s = Supplier.query.get_or_404(id)
    s.nama = request.form.get('nama')
    s.no_hp = request.form.get('no_hp')
    s.alamat = request.form.get('alamat')
    db.session.commit()
    flash('Supplier berhasil diupdate!', 'success')
    return redirect(url_for('supplier'))

@app.route('/supplier/hapus/<int:id>')
@login_required
def supplier_hapus(id):
    s = Supplier.query.get_or_404(id)
    db.session.delete(s)
    db.session.commit()
    flash('Supplier berhasil dihapus!', 'success')
    return redirect(url_for('supplier'))

@app.route('/api/supplier')
@login_required
def api_supplier():
    suppliers = Supplier.query.order_by(Supplier.nama.asc()).all()
    return jsonify([{'id': s.id, 'nama': s.nama, 'no_hp': s.no_hp} for s in suppliers])

# Sparepart Management
@app.route('/sparepart')
@login_required
def sparepart():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    cabang_id = get_user_cabang_id()
    
    query = Sparepart.query
    if current_user.role != 'admin':
        query = query.filter_by(cabang_id=current_user.cabang_id)
    elif cabang_id:
        query = query.filter_by(cabang_id=cabang_id)
    if search:
        query = query.filter(Sparepart.nama.ilike(f'%{search}%') | Sparepart.kode.ilike(f'%{search}%'))
    
    pagination = query.paginate(page=page, per_page=10, error_out=False)
    cabangs = Cabang.query.filter_by(aktif=True).all() if current_user.role == 'admin' else[]
    return render_template('sparepart.html', pagination=pagination, search=search, cabangs=cabangs, cabang_id=cabang_id)

@app.route('/sparepart/tambah', methods=['POST'])
@login_required
def sparepart_tambah():
    if current_user.role == 'kasir':
        flash('Akses ditolak! Kasir tidak diizinkan menambah data sparepart.', 'error')
        return redirect(url_for('sparepart'))
    kode = request.form.get('kode')
    nama = request.form.get('nama')
    harga_beli = float(request.form.get('harga_beli', 0))
    harga_jual = float(request.form.get('harga_jual', 0))
    harga_jual_2 = float(request.form.get('harga_jual_2', 0))
    harga_jual_3 = float(request.form.get('harga_jual_3', 0))
    stok = int(request.form.get('stok', 0))
    cabang_id = get_default_cabang_id()

    sp = Sparepart(kode=kode, nama=nama, harga_beli=harga_beli, harga_jual=harga_jual,
                   harga_jual_2=harga_jual_2, harga_jual_3=harga_jual_3, stok=stok, cabang_id=cabang_id)
    db.session.add(sp)
    db.session.commit()
    flash('Sparepart berhasil ditambahkan!', 'success')
    return redirect(url_for('sparepart'))

@app.route('/sparepart/edit/<int:id>', methods=['POST'])
@login_required
def sparepart_edit(id):
    if current_user.role == 'kasir':
        flash('Akses ditolak! Kasir tidak diizinkan mengubah data sparepart.', 'error')
        return redirect(url_for('sparepart'))

    sp = Sparepart.query.get_or_404(id)
    if current_user.role != 'admin' and sp.cabang_id != current_user.cabang_id:
        flash('Akses ditolak!', 'error')
        return redirect(url_for('sparepart'))

    sp.kode = request.form.get('kode')
    sp.nama = request.form.get('nama')
    sp.harga_beli = float(request.form.get('harga_beli', 0))
    sp.harga_jual = float(request.form.get('harga_jual', 0))
    sp.harga_jual_2 = float(request.form.get('harga_jual_2', 0))
    sp.harga_jual_3 = float(request.form.get('harga_jual_3', 0))
    sp.stok = int(request.form.get('stok', 0))
    db.session.commit()
    flash('Sparepart berhasil diupdate!', 'success')
    return redirect(url_for('sparepart'))

@app.route('/sparepart/hapus/<int:id>')
@login_required
def sparepart_hapus(id):
    if current_user.role == 'kasir':
        flash('Akses ditolak! Kasir tidak diizinkan menghapus data sparepart.', 'error')
        return redirect(url_for('sparepart'))

    sp = Sparepart.query.get_or_404(id)
    if current_user.role != 'admin' and sp.cabang_id != current_user.cabang_id:
        flash('Akses ditolak!', 'error')
        return redirect(url_for('sparepart'))

    db.session.delete(sp)
    db.session.commit()
    flash('Sparepart berhasil dihapus!', 'success')
    return redirect(url_for('sparepart'))

# --- EXPORT DATA SPAREPART KE CSV ---
@app.route('/sparepart/export')
@login_required
def sparepart_export():
    cabang_id = get_user_cabang_id()

    query = Sparepart.query
    if current_user.role != 'admin':
        query = query.filter_by(cabang_id=current_user.cabang_id)
    elif cabang_id:
        query = query.filter_by(cabang_id=cabang_id)
    data = query.order_by(Sparepart.nama).all()

    output = io.StringIO()
    output.write("sep=,\n")  # Bantu Excel membaca koma sebagai pemisah kolom
    writer = csv.writer(output, delimiter=',', lineterminator='\n')

    # Header kolom (sama urutannya dengan template import)
    writer.writerow(["kode", "nama", "harga_beli", "harga_jual", "harga_jual_2", "harga_jual_3", "stok"])
    for s in data:
        writer.writerow([s.kode, s.nama, int(s.harga_beli), int(s.harga_jual),
                         int(s.harga_jual_2), int(s.harga_jual_3), s.stok])

    response = Response(output.getvalue().encode('utf-8-sig'), mimetype="text/csv")
    filename = f"Data_Sparepart_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response

# --- DOWNLOAD TEMPLATE CONTOH IMPORT CSV ---
@app.route('/sparepart/template')
@login_required
def sparepart_template():
    output = io.StringIO()
    output.write("sep=,\n")
    writer = csv.writer(output, delimiter=',', lineterminator='\n')

    writer.writerow(["kode", "nama", "harga_beli", "harga_jual", "harga_jual_2", "harga_jual_3", "stok"])
    # Baris contoh untuk memandu pengguna (boleh dihapus sebelum import)
    writer.writerow(["SP001", "LCD Samsung A51", 250000, 350000, 340000, 330000, 10])
    writer.writerow(["SP002", "Baterai iPhone 11", 150000, 250000, 240000, 230000, 5])

    response = Response(output.getvalue().encode('utf-8-sig'), mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=Template_Import_Sparepart.csv"
    return response

# --- IMPORT DATA SPAREPART DARI CSV ---
@app.route('/sparepart/import', methods=['POST'])
@login_required
def sparepart_import():
    if current_user.role == 'kasir':
        flash('Akses ditolak! Kasir tidak diizinkan mengimpor data sparepart.', 'error')
        return redirect(url_for('sparepart'))

    file = request.files.get('file')
    if not file or file.filename == '':
        flash('Pilih file CSV terlebih dahulu!', 'error')
        return redirect(url_for('sparepart'))
    if not file.filename.lower().endswith('.csv'):
        flash('Format file harus .csv!', 'error')
        return redirect(url_for('sparepart'))

    # Tentukan cabang tujuan
    cabang_id = get_default_cabang_id()

    # Baca & decode file (utf-8-sig agar BOM dari Excel terbuang)
    try:
        content = file.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        content = file.read().decode('latin-1')

    # Buang baris "sep=,\n" jika ada (dihasilkan Excel)
    lines = content.splitlines()
    lines = [ln for ln in lines if not ln.lower().startswith('sep=')]
    if not lines:
        flash('File CSV kosong!', 'error')
        return redirect(url_for('sparepart'))

    # Deteksi pemisah: koma atau titik-koma
    delimiter = ';' if lines[0].count(';') > lines[0].count(',') else ','
    reader = csv.DictReader(lines, delimiter=delimiter)

    def to_num(val):
        if val is None:
            return 0
        val = str(val).strip().replace('.', '').replace(',', '').replace('Rp', '').replace(' ', '')
        return int(val) if val else 0

    ditambah, diupdate, gagal = 0, 0, 0
    for row in reader:
        # Normalisasi nama kolom (huruf kecil, tanpa spasi)
        row = {(k or '').strip().lower(): (v or '').strip() for k, v in row.items()}
        kode = row.get('kode', '')
        nama = row.get('nama', '')
        if not kode or not nama:
            gagal += 1
            continue

        try:
            harga_beli = to_num(row.get('harga_beli'))
            harga_jual = to_num(row.get('harga_jual'))
            harga_jual_2 = to_num(row.get('harga_jual_2'))
            harga_jual_3 = to_num(row.get('harga_jual_3'))
            stok = to_num(row.get('stok'))
        except (ValueError, TypeError):
            gagal += 1
            continue

        # Update jika kode sudah ada di cabang ini, selain itu buat baru
        sp = Sparepart.query.filter_by(kode=kode, cabang_id=cabang_id).first()
        if sp:
            sp.nama = nama
            sp.harga_beli = harga_beli
            sp.harga_jual = harga_jual
            sp.harga_jual_2 = harga_jual_2
            sp.harga_jual_3 = harga_jual_3
            sp.stok = stok
            diupdate += 1
        else:
            db.session.add(Sparepart(kode=kode, nama=nama, harga_beli=harga_beli,
                                     harga_jual=harga_jual, harga_jual_2=harga_jual_2,
                                     harga_jual_3=harga_jual_3, stok=stok, cabang_id=cabang_id))
            ditambah += 1

    db.session.commit()
    pesan = f'Import selesai: {ditambah} barang baru, {diupdate} diperbarui.'
    if gagal:
        pesan += f' {gagal} baris dilewati (kode/nama kosong atau tidak valid).'
    flash(pesan, 'success' if (ditambah or diupdate) else 'error')
    return redirect(url_for('sparepart', cabang_id=cabang_id if current_user.role == 'admin' else None))

# Jasa Service Management
@app.route('/jasa')
@login_required
def jasa():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    query = JasaService.query
    if search:
        query = query.filter(JasaService.nama.ilike(f'%{search}%'))
    pagination = query.paginate(page=page, per_page=10, error_out=False)
    return render_template('jasa.html', pagination=pagination, search=search)

@app.route('/jasa/tambah', methods=['POST'])
@login_required
@admin_required
def jasa_tambah():
    nama = request.form.get('nama')
    harga = float(request.form.get('harga', 0))
    jasa = JasaService(nama=nama, harga=harga)
    db.session.add(jasa)
    db.session.commit()
    flash('Jasa service berhasil ditambahkan!', 'success')
    return redirect(url_for('jasa'))

@app.route('/jasa/edit/<int:id>', methods=['POST'])
@login_required
@admin_required
def jasa_edit(id):
    jasa = JasaService.query.get_or_404(id)
    jasa.nama = request.form.get('nama')
    jasa.harga = float(request.form.get('harga', 0))
    db.session.commit()
    flash('Jasa service berhasil diupdate!', 'success')
    return redirect(url_for('jasa'))

@app.route('/jasa/hapus/<int:id>')
@login_required
@admin_required
def jasa_hapus(id):
    jasa = JasaService.query.get_or_404(id)
    db.session.delete(jasa)
    db.session.commit()
    flash('Jasa service berhasil dihapus!', 'success')
    return redirect(url_for('jasa'))

# Penjualan Sparepart
@app.route('/penjualan')
@login_required
def penjualan():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    cabang_id = get_user_cabang_id()
    
    query = Penjualan.query
    if current_user.role != 'admin':
        query = query.filter_by(cabang_id=current_user.cabang_id)
    elif cabang_id:
        query = query.filter_by(cabang_id=cabang_id)
    if search:
        query = query.filter(Penjualan.no_nota.ilike(f'%{search}%') | Penjualan.pelanggan.ilike(f'%{search}%'))
    
    query = query.order_by(Penjualan.tanggal.desc())
    pagination = query.paginate(page=page, per_page=10, error_out=False)
    cabangs = Cabang.query.filter_by(aktif=True).all() if current_user.role == 'admin' else[]
    
    # Get spareparts dan pelanggan for current cabang
    if current_user.role == 'admin':
        sp_query = Sparepart.query.filter(Sparepart.stok > 0).all() if not cabang_id else Sparepart.query.filter_by(cabang_id=cabang_id).filter(Sparepart.stok > 0).all()
        pelanggan_query = Pelanggan.query.all() if not cabang_id else Pelanggan.query.filter_by(cabang_id=cabang_id).all()
    else:
        sp_query = Sparepart.query.filter_by(cabang_id=current_user.cabang_id).filter(Sparepart.stok > 0).all()
        pelanggan_query = Pelanggan.query.filter_by(cabang_id=current_user.cabang_id).all()
    
    spareparts =[{'id': s.id, 'nama': s.nama, 'kode': s.kode, 'harga_jual': s.harga_jual, 'harga_jual_2': s.harga_jual_2, 'harga_jual_3': s.harga_jual_3, 'stok': s.stok} for s in sp_query]
    spareparts_obj = sp_query
    
    # Format data Pelanggan
    pelanggans =[{'id': p.id, 'nama': p.nama, 'no_hp': p.no_hp} for p in pelanggan_query]
    
    return render_template('penjualan.html', pagination=pagination, search=search, cabangs=cabangs, 
                         cabang_id=cabang_id, spareparts=spareparts, spareparts_obj=spareparts_obj,
                         pelanggans=pelanggans)

@app.route('/penjualan/tambah', methods=['POST'])
@login_required
def penjualan_tambah():
    cabang_id = current_user.cabang_id or get_default_cabang_id()
    
    pelanggan = request.form.get('pelanggan', 'Umum')
    diskon = float(request.form.get('diskon', 0))
    
    items = request.form.getlist('items[]')
    quantities = request.form.getlist('quantities[]')
    harga_levels = request.form.getlist('harga_levels[]')

    if not items:
        flash('Pilih minimal 1 sparepart!', 'error')
        return redirect(url_for('penjualan'))

    no_nota = generate_nota('PJ', Penjualan)
    penjualan = Penjualan(no_nota=no_nota, pelanggan=pelanggan, cabang_id=cabang_id,
                         user_id=current_user.id, diskon=diskon)
    db.session.add(penjualan)
    db.session.flush()

    subtotal_transaksi = 0
    total_laba = 0

    for i, item_id in enumerate(items):
        sp = Sparepart.query.get(int(item_id))
        qty = int(quantities[i])
        level = harga_levels[i] if i < len(harga_levels) else '1'
        if level == '2':
            harga = sp.harga_jual_2 if sp.harga_jual_2 > 0 else sp.harga_jual
        elif level == '3':
            harga = sp.harga_jual_3 if sp.harga_jual_3 > 0 else sp.harga_jual
        else:
            harga = sp.harga_jual
        if sp and sp.stok >= qty:
            subtotal_item = harga * qty
            laba_item = (harga - sp.harga_beli) * qty

            item = PenjualanItem(penjualan_id=penjualan.id, sparepart_id=sp.id, jumlah=qty,
                               harga_beli=sp.harga_beli, harga_jual=harga,
                               subtotal=subtotal_item, laba=laba_item)
            db.session.add(item)
            sp.stok -= qty
            
            subtotal_transaksi += subtotal_item
            total_laba += laba_item
    
    total_akhir = subtotal_transaksi - diskon
    if total_akhir < 0: total_akhir = 0
    
    # Ambil data pembayaran
    metode_pembayaran = request.form.get('metode_pembayaran', 'lunas')
    dibayar_input = request.form.get('dibayar', '')
    
    if metode_pembayaran == 'lunas':
        dibayar = total_akhir
        sisa = 0
        status_pembayaran = 'lunas'
    else:
        dibayar = float(dibayar_input) if dibayar_input else 0
        sisa = total_akhir - dibayar
        status_pembayaran = 'kasbon' if sisa > 0 else 'lunas'
        if sisa < 0:
            dibayar = total_akhir
            sisa = 0
            status_pembayaran = 'lunas'
    
    penjualan.total = total_akhir
    penjualan.laba = total_laba - diskon
    penjualan.status_pembayaran = status_pembayaran
    penjualan.dibayar = dibayar
    penjualan.sisa = sisa
    
    # Masukkan Kas Masuk HANYA sebesar uang yang diterima (DP / Lunas)
    if dibayar > 0:
        kas = KasMasuk(sumber='penjualan', referensi_id=penjualan.id, nominal=dibayar,
                      keterangan=f'Penjualan {no_nota} ({"Lunas" if status_pembayaran=="lunas" else "DP Kasbon"})', 
                      cabang_id=cabang_id, user_id=current_user.id)
        db.session.add(kas)
        
    db.session.commit()
    
    flash(f'Penjualan {no_nota} berhasil disimpan!', 'success')
    return redirect(url_for('penjualan'))

@app.route('/penjualan/detail/<int:id>')
@login_required
def penjualan_detail(id):
    penjualan = Penjualan.query.get_or_404(id)
    if current_user.role != 'admin' and penjualan.cabang_id != current_user.cabang_id:
        return jsonify({'error': 'Akses ditolak'}), 403
    
    items =[{'nama': i.sparepart.nama, 'jumlah': i.jumlah, 'harga': i.harga_jual, 'subtotal': i.subtotal} for i in penjualan.items]

    # Coba cari no HP pelanggan dari data Pelanggan (best-effort, berdasarkan kecocokan nama)
    no_hp = ''
    if penjualan.pelanggan and penjualan.pelanggan.lower() != 'umum':
        pel = Pelanggan.query.filter(Pelanggan.nama.ilike(penjualan.pelanggan)).first()
        if pel and pel.no_hp:
            no_hp = pel.no_hp

    return jsonify({
        'no_nota': penjualan.no_nota, 
        'tanggal': penjualan.tanggal.strftime('%d/%m/%Y %H:%M'),
        'pelanggan': penjualan.pelanggan, 
        'no_hp': no_hp,
        'subtotal': sum(i.subtotal for i in penjualan.items),
        'diskon': penjualan.diskon,
        'status_pembayaran': penjualan.status_pembayaran,
        'dibayar': penjualan.dibayar,
        'sisa': penjualan.sisa,
        'kasir': penjualan.user.nama_lengkap,
        'total': penjualan.total, 
        'items': items
    })

@app.route('/penjualan/hapus/<int:id>')
@login_required
def penjualan_hapus(id):
    pj = Penjualan.query.get_or_404(id)
    if current_user.role != 'admin' and pj.cabang_id != current_user.cabang_id:
        flash('Akses ditolak!', 'error')
        return redirect(url_for('penjualan'))
    for item in pj.items:
        item.sparepart.stok += item.jumlah
    KasMasuk.query.filter_by(sumber='penjualan', referensi_id=pj.id).delete()
    KasMasuk.query.filter_by(sumber='piutang', referensi_id=pj.id).delete() # Hapus juga cicilan dari Kas
    db.session.delete(pj)
    db.session.commit()
    flash('Penjualan berhasil dihapus!', 'success')
    return redirect(url_for('penjualan'))


# --- PEMBELIAN BARANG (SPAREPART) ---
@app.route('/pembelian')
@login_required
def pembelian():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    cabang_id = get_user_cabang_id()
    
    query = Pembelian.query
    if current_user.role != 'admin':
        query = query.filter_by(cabang_id=current_user.cabang_id)
    elif cabang_id:
        query = query.filter_by(cabang_id=cabang_id)
    if search:
        query = query.filter(Pembelian.no_nota.ilike(f'%{search}%'))
    
    query = query.order_by(Pembelian.tanggal.desc())
    pagination = query.paginate(page=page, per_page=10, error_out=False)
    cabangs = Cabang.query.filter_by(aktif=True).all() if current_user.role == 'admin' else []
    
    if current_user.role == 'admin':
        sp_query = Sparepart.query.all() if not cabang_id else Sparepart.query.filter_by(cabang_id=cabang_id).all()
    else:
        sp_query = Sparepart.query.filter_by(cabang_id=current_user.cabang_id).all()
        
    spareparts = [{'id': s.id, 'nama': s.nama, 'kode': s.kode, 'harga_beli': s.harga_beli, 'stok': s.stok} for s in sp_query]
    suppliers = [{'id': s.id, 'nama': s.nama} for s in Supplier.query.all()]
    
    return render_template('pembelian.html', pagination=pagination, search=search, cabangs=cabangs, 
                         cabang_id=cabang_id, spareparts=spareparts, suppliers=suppliers)

@app.route('/pembelian/tambah', methods=['POST'])
@login_required
def pembelian_tambah():
    cabang_id = current_user.cabang_id or get_default_cabang_id()
    
    supplier_id = request.form.get('supplier_id') or None
    items = request.form.getlist('items[]')
    quantities = request.form.getlist('quantities[]')
    hargas = request.form.getlist('hargas[]')
    
    if not items:
        flash('Pilih minimal 1 sparepart!', 'error')
        return redirect(url_for('pembelian'))
    
    no_nota = generate_nota('PB', Pembelian)
    pb = Pembelian(no_nota=no_nota, supplier_id=supplier_id, cabang_id=cabang_id, user_id=current_user.id)
    db.session.add(pb)
    db.session.flush()
    
    total_akhir = 0
    for i, item_id in enumerate(items):
        sp = Sparepart.query.get(int(item_id))
        qty = int(quantities[i])
        harga = float(hargas[i]) if i < len(hargas) else sp.harga_beli
        if sp:
            subtotal = harga * qty
            item = PembelianItem(pembelian_id=pb.id, sparepart_id=sp.id, jumlah=qty,
                               harga_beli=harga, subtotal=subtotal)
            db.session.add(item)
            # Update stok and harga_beli
            sp.stok += qty
            sp.harga_beli = harga
            total_akhir += subtotal
    
    # Ambil data pembayaran
    metode_pembayaran = request.form.get('metode_pembayaran', 'lunas')
    dibayar_input = request.form.get('dibayar', '')
    
    if metode_pembayaran == 'lunas':
        dibayar = total_akhir
        sisa = 0
        status_pembayaran = 'lunas'
    else:
        dibayar = float(dibayar_input) if dibayar_input else 0
        sisa = total_akhir - dibayar
        status_pembayaran = 'belum lunas' if sisa > 0 else 'lunas'
        if sisa < 0:
            dibayar = total_akhir
            sisa = 0
            status_pembayaran = 'lunas'
    
    pb.total = total_akhir
    pb.status_pembayaran = status_pembayaran
    pb.dibayar = dibayar
    pb.sisa = sisa
    
    # Masukkan Kas Keluar HANYA sebesar uang yang dikeluarkan
    if dibayar > 0:
        kas = KasKeluar(keperluan='pembelian', nominal=dibayar,
                      keterangan=f'Pembelian {no_nota} ({"Lunas" if status_pembayaran=="lunas" else "DP/Cicilan"})',
                      cabang_id=cabang_id, user_id=current_user.id)
        db.session.add(kas)
        
    db.session.commit()
    flash(f'Pembelian {no_nota} berhasil disimpan!', 'success')
    return redirect(url_for('pembelian'))

@app.route('/pembelian/detail/<int:id>')
@login_required
def pembelian_detail(id):
    pb = Pembelian.query.get_or_404(id)
    if current_user.role != 'admin' and pb.cabang_id != current_user.cabang_id:
        return jsonify({'error': 'Akses ditolak'}), 403
    
    items = [{'nama': i.sparepart.nama, 'jumlah': i.jumlah, 'harga': i.harga_beli, 'subtotal': i.subtotal} for i in pb.items]
    supplier_nama = pb.supplier.nama if pb.supplier else 'Umum'
    
    return jsonify({
        'no_nota': pb.no_nota, 
        'tanggal': pb.tanggal.strftime('%d/%m/%Y %H:%M'),
        'supplier': supplier_nama, 
        'total': pb.total, 
        'dibayar': pb.dibayar,
        'sisa': pb.sisa,
        'status_pembayaran': pb.status_pembayaran,
        'items': items
    })

@app.route('/pembelian/hapus/<int:id>')
@login_required
def pembelian_hapus(id):
    pb = Pembelian.query.get_or_404(id)
    if current_user.role != 'admin' and pb.cabang_id != current_user.cabang_id:
        flash('Akses ditolak!', 'error')
        return redirect(url_for('pembelian'))
    for item in pb.items:
        item.sparepart.stok -= item.jumlah
    KasKeluar.query.filter_by(keperluan='pembelian', referensi_id=pb.id).delete()
    KasKeluar.query.filter_by(keperluan='hutang', referensi_id=pb.id).delete()
    db.session.delete(pb)
    db.session.commit()
    flash('Pembelian berhasil dihapus!', 'success')
    return redirect(url_for('pembelian'))

@app.route('/pembelian/bayar/<int:id>', methods=['POST'])
@login_required
def pembelian_bayar(id):
    pb = Pembelian.query.get_or_404(id)
    if current_user.role != 'admin' and pb.cabang_id != current_user.cabang_id:
        flash('Akses ditolak!', 'error')
        return redirect(url_for('pembelian'))
        
    nominal = float(request.form.get('nominal', 0))
    if nominal <= 0 or nominal > pb.sisa:
        flash('Nominal tidak valid!', 'error')
        return redirect(url_for('pembelian'))
        
    bayar = PembayaranHutang(pembelian_id=pb.id, nominal=nominal, user_id=current_user.id)
    db.session.add(bayar)
    
    pb.dibayar += nominal
    pb.sisa -= nominal
    if pb.sisa <= 0:
        pb.status_pembayaran = 'lunas'
        pb.sisa = 0
        
    kas = KasKeluar(keperluan='hutang', referensi_id=pb.id, nominal=nominal,
                  keterangan=f'Pembayaran Hutang {pb.no_nota}', cabang_id=pb.cabang_id, user_id=current_user.id)
    db.session.add(kas)
    
    db.session.commit()
    flash(f'Pembayaran hutang sejumlah {nominal} berhasil!', 'success')
    return redirect(url_for('pembelian'))

@app.route('/pembelian/history/<int:id>')
@login_required
def pembelian_history(id):
    pb = Pembelian.query.get_or_404(id)
    history = [{'tanggal': h.tanggal.strftime('%d/%m/%Y %H:%M'), 'nominal': h.nominal, 'user': h.user.nama_lengkap} for h in pb.pembayaran_history]
    return jsonify(history)

# Service HP
@app.route('/service')
@login_required
def service():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    cabang_id = get_user_cabang_id()
    
    query = Service.query
    if current_user.role != 'admin':
        query = query.filter_by(cabang_id=current_user.cabang_id)
    elif cabang_id:
        query = query.filter_by(cabang_id=cabang_id)
    if search:
        query = query.filter(Service.no_nota.ilike(f'%{search}%') | Service.pelanggan.ilike(f'%{search}%'))
    
    query = query.order_by(Service.tanggal.desc())
    pagination = query.paginate(page=page, per_page=10, error_out=False)
    cabangs = Cabang.query.filter_by(aktif=True).all() if current_user.role == 'admin' else[]
    
    if current_user.role == 'admin':
        sp_query = Sparepart.query.filter(Sparepart.stok > 0).all() if not cabang_id else Sparepart.query.filter_by(cabang_id=cabang_id).filter(Sparepart.stok > 0).all()
        teknisis = User.query.filter(User.role.in_(['teknisi', 'admin'])).all()
        pelanggan_query = Pelanggan.query.all() if not cabang_id else Pelanggan.query.filter_by(cabang_id=cabang_id).all()
    else:
        sp_query = Sparepart.query.filter_by(cabang_id=current_user.cabang_id).filter(Sparepart.stok > 0).all()
        teknisis = User.query.filter(User.role.in_(['teknisi', 'admin']), User.cabang_id==current_user.cabang_id).all()
        pelanggan_query = Pelanggan.query.filter_by(cabang_id=current_user.cabang_id).all()
    
    spareparts =[{'id': s.id, 'nama': s.nama, 'kode': s.kode, 'harga_jual': s.harga_jual, 'stok': s.stok} for s in sp_query]
    pelanggans =[{'id': p.id, 'nama': p.nama, 'no_hp': p.no_hp} for p in pelanggan_query]
    
    jasas = JasaService.query.all()
    return render_template('service.html', pagination=pagination, search=search, cabangs=cabangs,
                         cabang_id=cabang_id, spareparts=spareparts, jasas=jasas, teknisis=teknisis,
                         pelanggans=pelanggans)

@app.route('/service/tambah', methods=['POST'])
@login_required
def service_tambah():
    cabang_id = current_user.cabang_id or get_default_cabang_id()
    
    no_nota = generate_nota('SV', Service)
    pelanggan = request.form.get('pelanggan')
    no_hp = request.form.get('no_hp')
    merk_hp = request.form.get('merk_hp')
    kerusakan = request.form.get('kerusakan')
    teknisi_id = request.form.get('teknisi_id') or None
    
    service = Service(no_nota=no_nota, pelanggan=pelanggan, no_hp=no_hp, merk_hp=merk_hp,
                     kerusakan=kerusakan, teknisi_id=teknisi_id, cabang_id=cabang_id, user_id=current_user.id)
    db.session.add(service)
    db.session.flush()
    
    jasa_ids = request.form.getlist('jasa_ids[]')
    total_jasa = 0
    for jid in jasa_ids:
        jasa = JasaService.query.get(int(jid))
        if jasa:
            sj = ServiceJasa(service_id=service.id, jasa_id=jasa.id, harga=jasa.harga)
            db.session.add(sj)
            total_jasa += jasa.harga
    
    sp_ids = request.form.getlist('sp_ids[]')
    sp_qtys = request.form.getlist('sp_qtys[]')
    total_sp = 0
    laba_sp = 0
    for i, sid in enumerate(sp_ids):
        sp = Sparepart.query.get(int(sid))
        qty = int(sp_qtys[i]) if i < len(sp_qtys) else 1
        if sp and sp.stok >= qty:
            subtotal = sp.harga_jual * qty
            laba = (sp.harga_jual - sp.harga_beli) * qty
            ss = ServiceSparepart(service_id=service.id, sparepart_id=sp.id, jumlah=qty,
                                 harga_beli=sp.harga_beli, harga_jual=sp.harga_jual, subtotal=subtotal, laba=laba)
            db.session.add(ss)
            sp.stok -= qty
            total_sp += subtotal
            laba_sp += laba
    
    service.total_jasa = total_jasa
    service.laba_jasa = total_jasa
    service.total_sparepart = total_sp
    service.laba_sparepart = laba_sp
    service.total = total_jasa + total_sp
    
    db.session.commit()
    flash(f'Service {no_nota} berhasil disimpan!', 'success')
    return redirect(url_for('service'))

@app.route('/service/update_status/<int:id>', methods=['POST'])
@login_required
def service_update_status(id):
    sv = Service.query.get_or_404(id)
    if current_user.role != 'admin' and sv.cabang_id != current_user.cabang_id:
        flash('Akses ditolak!', 'error')
        return redirect(url_for('service'))

    status = request.form.get('status')
    sv.status = status

    existing_kas = KasMasuk.query.filter_by(sumber='service', referensi_id=sv.id).first()

    # Kas masuk HANYA saat status "sudah diambil" (transaksi selesai)
    if status == 'sudah diambil':
        status_pembayaran = request.form.get('status_pembayaran', 'lunas')
        if status_pembayaran == 'belum lunas':
            dibayar = float(request.form.get('dibayar', 0) or 0)
            if dibayar > sv.total:
                dibayar = sv.total
            if dibayar < 0:
                dibayar = 0
            sv.dibayar = dibayar
            sv.sisa = sv.total - dibayar
            sv.status_pembayaran = 'lunas' if sv.sisa <= 0 else 'belum lunas'
        else:
            sv.dibayar = sv.total
            sv.sisa = 0
            sv.status_pembayaran = 'lunas'

        # Kas masuk hanya sebesar uang yang benar-benar diterima
        if not existing_kas:
            if sv.dibayar > 0:
                kas = KasMasuk(sumber='service', referensi_id=sv.id, nominal=sv.dibayar,
                              keterangan=f'Service {sv.no_nota} - Diambil pelanggan',
                              cabang_id=sv.cabang_id, user_id=current_user.id)
                db.session.add(kas)
        else:
            existing_kas.nominal = sv.dibayar
    else:
        # Status lain: hapus kas masuk jika ada (proses/selesai dikerjakan/retur)
        if existing_kas:
            db.session.delete(existing_kas)
        sv.status_pembayaran = 'lunas'
        sv.dibayar = 0
        sv.sisa = 0

    db.session.commit()
    flash('Status service berhasil diupdate!', 'success')
    return redirect(url_for('service'))

@app.route('/service/tambah_sparepart/<int:id>', methods=['POST'])
@login_required
def service_tambah_sparepart(id):
    """Tambah sparepart ke service yang sudah ada (edit saat proses)"""
    sv = Service.query.get_or_404(id)
    if current_user.role != 'admin' and sv.cabang_id != current_user.cabang_id:
        flash('Akses ditolak!', 'error')
        return redirect(url_for('service'))

    sp_ids = request.form.getlist('sp_ids[]')
    sp_qtys = request.form.getlist('sp_qtys[]')
    tambah_sp = 0
    tambah_laba = 0
    for i, sid in enumerate(sp_ids):
        sp = Sparepart.query.get(int(sid))
        qty = int(sp_qtys[i]) if i < len(sp_qtys) else 1
        if sp and sp.stok >= qty:
            subtotal = sp.harga_jual * qty
            laba = (sp.harga_jual - sp.harga_beli) * qty
            ss = ServiceSparepart(service_id=sv.id, sparepart_id=sp.id, jumlah=qty,
                                 harga_beli=sp.harga_beli, harga_jual=sp.harga_jual,
                                 subtotal=subtotal, laba=laba)
            db.session.add(ss)
            sp.stok -= qty
            tambah_sp += subtotal
            tambah_laba += laba

    sv.total_sparepart = (sv.total_sparepart or 0) + tambah_sp
    sv.laba_sparepart = (sv.laba_sparepart or 0) + tambah_laba
    sv.total = sv.total_jasa + sv.total_sparepart
    db.session.commit()
    flash('Sparepart tambahan berhasil disimpan!', 'success')
    return redirect(url_for('service'))

@app.route('/service/detail/<int:id>')
@login_required
def service_detail(id):
    sv = Service.query.get_or_404(id)
    if current_user.role != 'admin' and sv.cabang_id != current_user.cabang_id:
        return jsonify({'error': 'Akses ditolak'}), 403
    jasas =[{'nama': j.jasa.nama, 'harga': j.harga} for j in sv.items_jasa]
    sps =[{'nama': s.sparepart.nama, 'jumlah': s.jumlah, 'harga': s.harga_jual, 'subtotal': s.subtotal} for s in sv.items_sparepart]
    return jsonify({'no_nota': sv.no_nota, 'tanggal': sv.tanggal.strftime('%d/%m/%Y %H:%M'),
                   'pelanggan': sv.pelanggan, 'no_hp': sv.no_hp, 'merk_hp': sv.merk_hp,
                   'kerusakan': sv.kerusakan, 'status': sv.status, 'total': sv.total,
                   'status_pembayaran': sv.status_pembayaran, 'dibayar': sv.dibayar, 'sisa': sv.sisa,
                   'jasas': jasas, 'spareparts': sps})

@app.route('/service/hapus/<int:id>')
@login_required
def service_hapus(id):
    sv = Service.query.get_or_404(id)
    if current_user.role != 'admin' and sv.cabang_id != current_user.cabang_id:
        flash('Akses ditolak!', 'error')
        return redirect(url_for('service'))
    for item in sv.items_sparepart:
        item.sparepart.stok += item.jumlah
    KasMasuk.query.filter_by(sumber='service', referensi_id=sv.id).delete()
    db.session.delete(sv)
    db.session.commit()
    flash('Service berhasil dihapus!', 'success')
    return redirect(url_for('service'))

# Retur
@app.route('/retur')
@login_required
def retur():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    cabang_id = get_user_cabang_id()
    
    query = Retur.query
    if current_user.role != 'admin':
        query = query.filter_by(cabang_id=current_user.cabang_id)
    elif cabang_id:
        query = query.filter_by(cabang_id=cabang_id)
    if search:
        query = query.filter(Retur.no_retur.ilike(f'%{search}%'))
    
    query = query.order_by(Retur.tanggal.desc())
    pagination = query.paginate(page=page, per_page=10, error_out=False)
    cabangs = Cabang.query.filter_by(aktif=True).all() if current_user.role == 'admin' else[]
    
    if current_user.role == 'admin':
        pj_query = Penjualan.query.filter_by(status='selesai').all() if not cabang_id else Penjualan.query.filter_by(cabang_id=cabang_id, status='selesai').all()
        sv_query = Service.query.filter_by(status='sudah diambil').all() if not cabang_id else Service.query.filter_by(cabang_id=cabang_id, status='sudah diambil').all()
        pb_query = Pembelian.query.all() if not cabang_id else Pembelian.query.filter_by(cabang_id=cabang_id).all()
        sp_query = Sparepart.query.all() if not cabang_id else Sparepart.query.filter_by(cabang_id=cabang_id).all()
    else:
        pj_query = Penjualan.query.filter_by(cabang_id=current_user.cabang_id, status='selesai').all()
        sv_query = Service.query.filter_by(cabang_id=current_user.cabang_id, status='sudah diambil').all()
        pb_query = Pembelian.query.filter_by(cabang_id=current_user.cabang_id).all()
        sp_query = Sparepart.query.filter_by(cabang_id=current_user.cabang_id).all()

    # Nota yang sudah diretur → hilangkan dari dropdown
    retur_penjualan_ids = set(r.referensi_id for r in Retur.query.filter_by(tipe='pelanggan', referensi_tipe='penjualan').all() if r.referensi_id)
    retur_service_ids = set(r.referensi_id for r in Retur.query.filter_by(tipe='pelanggan', referensi_tipe='service').all() if r.referensi_id)
    retur_pembelian_ids = set(r.referensi_id for r in Retur.query.filter_by(tipe='supplier').all() if r.referensi_id)

    penjualans = [{'id': p.id, 'no_nota': p.no_nota} for p in pj_query if p.id not in retur_penjualan_ids]
    services = [{'id': s.id, 'no_nota': s.no_nota} for s in sv_query if s.id not in retur_service_ids]
    pembelians = [{'id': p.id, 'no_nota': p.no_nota} for p in pb_query if p.id not in retur_pembelian_ids]
    spareparts = sp_query

    return render_template('retur.html', pagination=pagination, search=search, cabangs=cabangs,
                         cabang_id=cabang_id, penjualans=penjualans, services=services,
                         pembelians=pembelians, spareparts=spareparts)

@app.route('/retur/tambah', methods=['POST'])
@login_required
def retur_tambah():
    cabang_id = get_default_cabang_id()

    tipe = request.form.get('tipe')
    no_retur = generate_nota('RT', Retur, 'no_retur')

    if tipe == 'pelanggan':
        ref_tipe = request.form.get('ref_tipe')
        ref_id = request.form.get('ref_id')
        sp_id = request.form.get('sparepart_id')
        jumlah = int(request.form.get('jumlah', 1))

        # Nota wajib diisi
        if not ref_id:
            flash('Nomor nota wajib dipilih untuk retur pelanggan!', 'error')
            return redirect(url_for('retur'))

        # Cegah retur berulang untuk nota + sparepart yang sama
        existing_retur = Retur.query.filter_by(
            referensi_id=int(ref_id), referensi_tipe=ref_tipe,
            sparepart_id=int(sp_id), tipe='pelanggan'
        ).first()
        if existing_retur:
            flash('Retur untuk nota dan sparepart ini sudah pernah dilakukan!', 'error')
            return redirect(url_for('retur'))

        sp = Sparepart.query.get(sp_id)
        if not sp:
            flash('Sparepart tidak ditemukan!', 'error')
            return redirect(url_for('retur'))
        nominal = sp.harga_jual * jumlah
        sp.stok += jumlah

        retur = Retur(no_retur=no_retur, tipe=tipe, referensi_tipe=ref_tipe, referensi_id=int(ref_id),
                     sparepart_id=int(sp_id), jumlah=jumlah, nominal=nominal,
                     keterangan=request.form.get('keterangan'), cabang_id=cabang_id, user_id=current_user.id)
        db.session.add(retur)

    elif tipe == 'supplier':
        pb_id = request.form.get('pembelian_id')
        sp_id = request.form.get('sparepart_id')
        jumlah = int(request.form.get('jumlah', 1))

        # Nota pembelian wajib diisi
        if not pb_id:
            flash('Nomor nota pembelian wajib dipilih untuk retur ke supplier!', 'error')
            return redirect(url_for('retur'))

        # Cegah retur berulang untuk nota pembelian + sparepart yang sama
        existing_retur = Retur.query.filter_by(
            referensi_id=int(pb_id), referensi_tipe='pembelian',
            sparepart_id=int(sp_id), tipe='supplier'
        ).first()
        if existing_retur:
            flash('Retur ke supplier untuk nota dan sparepart ini sudah pernah dilakukan!', 'error')
            return redirect(url_for('retur'))

        sp = Sparepart.query.get(sp_id)
        if not sp:
            flash('Sparepart tidak ditemukan!', 'error')
            return redirect(url_for('retur'))
        nominal = sp.harga_beli * jumlah
        sp.stok -= jumlah

        retur = Retur(no_retur=no_retur, tipe=tipe, referensi_tipe='pembelian', referensi_id=int(pb_id),
                     sparepart_id=int(sp_id), jumlah=jumlah, nominal=nominal,
                     keterangan=request.form.get('keterangan'), cabang_id=cabang_id, user_id=current_user.id)
        db.session.add(retur)
    else:
        flash('Tipe retur tidak valid!', 'error')
        return redirect(url_for('retur'))

    db.session.commit()
    flash(f'Retur {no_retur} berhasil disimpan!', 'success')
    return redirect(url_for('retur'))

@app.route('/retur/hapus/<int:id>')
@login_required
def retur_hapus(id):
    rt = Retur.query.get_or_404(id)
    if current_user.role != 'admin' and rt.cabang_id != current_user.cabang_id:
        flash('Akses ditolak!', 'error')
        return redirect(url_for('retur'))
    if rt.sparepart:
        if rt.tipe == 'pelanggan':
            rt.sparepart.stok -= rt.jumlah
        else:
            rt.sparepart.stok += rt.jumlah
    db.session.delete(rt)
    db.session.commit()
    flash('Retur berhasil dihapus!', 'success')
    return redirect(url_for('retur'))

# Kas Masuk
@app.route('/kas-masuk')
@login_required
def kas_masuk():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    cabang_id = get_user_cabang_id()
    
    query = KasMasuk.query
    if current_user.role != 'admin':
        query = query.filter_by(cabang_id=current_user.cabang_id)
    elif cabang_id:
        query = query.filter_by(cabang_id=cabang_id)
    if search:
        query = query.filter(KasMasuk.keterangan.ilike(f'%{search}%'))
    
    query = query.order_by(KasMasuk.tanggal.desc())
    pagination = query.paginate(page=page, per_page=10, error_out=False)
    cabangs = Cabang.query.filter_by(aktif=True).all() if current_user.role == 'admin' else[]
    
    return render_template('kas_masuk.html', pagination=pagination, search=search, cabangs=cabangs, cabang_id=cabang_id)

@app.route('/kas-masuk/tambah', methods=['POST'])
@login_required
def kas_masuk_tambah():
    cabang_id = get_default_cabang_id()
    
    nominal = float(request.form.get('nominal', 0))
    keterangan = request.form.get('keterangan')
    
    kas = KasMasuk(sumber='manual', nominal=nominal, keterangan=keterangan,
                  cabang_id=cabang_id, user_id=current_user.id)
    db.session.add(kas)
    db.session.commit()
    flash('Kas masuk berhasil ditambahkan!', 'success')
    return redirect(url_for('kas_masuk'))

@app.route('/kas-masuk/hapus/<int:id>')
@login_required
def kas_masuk_hapus(id):
    kas = KasMasuk.query.get_or_404(id)
    if current_user.role != 'admin' and kas.cabang_id != current_user.cabang_id:
        flash('Akses ditolak!', 'error')
        return redirect(url_for('kas_masuk'))
    if kas.sumber != 'manual':
        flash('Hanya kas manual yang bisa dihapus!', 'error')
        return redirect(url_for('kas_masuk'))
    db.session.delete(kas)
    db.session.commit()
    flash('Kas masuk berhasil dihapus!', 'success')
    return redirect(url_for('kas_masuk'))

# Kas Keluar
@app.route('/kas-keluar')
@login_required
def kas_keluar():
    page = request.args.get('page', 1, type=int)
    search = request.args.get('search', '')
    cabang_id = get_user_cabang_id()
    
    query = KasKeluar.query
    if current_user.role != 'admin':
        query = query.filter_by(cabang_id=current_user.cabang_id)
    elif cabang_id:
        query = query.filter_by(cabang_id=cabang_id)
    if search:
        query = query.filter(KasKeluar.keterangan.ilike(f'%{search}%') | KasKeluar.keperluan.ilike(f'%{search}%'))
    
    query = query.order_by(KasKeluar.tanggal.desc())
    pagination = query.paginate(page=page, per_page=10, error_out=False)
    cabangs = Cabang.query.filter_by(aktif=True).all() if current_user.role == 'admin' else[]
    
    return render_template('kas_keluar.html', pagination=pagination, search=search, cabangs=cabangs, cabang_id=cabang_id)

@app.route('/kas-keluar/tambah', methods=['POST'])
@login_required
def kas_keluar_tambah():
    cabang_id = get_default_cabang_id()
    
    keperluan = request.form.get('keperluan')
    nominal = float(request.form.get('nominal', 0))
    keterangan = request.form.get('keterangan')
    
    kas = KasKeluar(keperluan=keperluan, nominal=nominal, keterangan=keterangan,
                   cabang_id=cabang_id, user_id=current_user.id)
    db.session.add(kas)
    db.session.commit()
    flash('Kas keluar berhasil ditambahkan!', 'success')
    return redirect(url_for('kas_keluar'))

@app.route('/kas-keluar/hapus/<int:id>')
@login_required
def kas_keluar_hapus(id):
    kas = KasKeluar.query.get_or_404(id)
    if current_user.role != 'admin' and kas.cabang_id != current_user.cabang_id:
        flash('Akses ditolak!', 'error')
        return redirect(url_for('kas_keluar'))
    db.session.delete(kas)
    db.session.commit()
    flash('Kas keluar berhasil dihapus!', 'success')
    return redirect(url_for('kas_keluar'))

# Laporan
@app.route('/laporan')
@login_required
def laporan():
    cabang_id = get_user_cabang_id()
    tipe = request.args.get('tipe', 'harian')
    tanggal = request.args.get('tanggal', datetime.now().strftime('%Y-%m-%d'))
    bulan = request.args.get('bulan', datetime.now().strftime('%Y-%m'))
    tgl_dari = request.args.get('tgl_dari', '')
    tgl_sampai = request.args.get('tgl_sampai', '')

    cabangs = Cabang.query.filter_by(aktif=True).all() if current_user.role == 'admin' else []
    if current_user.role != 'admin':
        cabang_id = current_user.cabang_id

    data = {'penjualan': [], 'pembelian': [], 'service': [], 'kas_masuk': [], 'kas_keluar':[], 'stok':[]}
    summary = {}

    if tipe == 'harian':
        tgl = datetime.strptime(tanggal, '%Y-%m-%d').date()
        pj_query = Penjualan.query.filter(func.date(Penjualan.tanggal) == tgl)
        pb_query = Pembelian.query.filter(func.date(Pembelian.tanggal) == tgl)
        sv_query = Service.query.filter(func.date(Service.tanggal) == tgl)
        km_query = KasMasuk.query.filter(func.date(KasMasuk.tanggal) == tgl)
        kk_query = KasKeluar.query.filter(func.date(KasKeluar.tanggal) == tgl)
    elif tipe == 'bulanan':
        tahun, bln = bulan.split('-')
        pj_query = Penjualan.query.filter(func.extract('year', Penjualan.tanggal) == int(tahun), func.extract('month', Penjualan.tanggal) == int(bln))
        pb_query = Pembelian.query.filter(func.extract('year', Pembelian.tanggal) == int(tahun), func.extract('month', Pembelian.tanggal) == int(bln))
        sv_query = Service.query.filter(func.extract('year', Service.tanggal) == int(tahun), func.extract('month', Service.tanggal) == int(bln))
        km_query = KasMasuk.query.filter(func.extract('year', KasMasuk.tanggal) == int(tahun), func.extract('month', KasMasuk.tanggal) == int(bln))
        kk_query = KasKeluar.query.filter(func.extract('year', KasKeluar.tanggal) == int(tahun), func.extract('month', KasKeluar.tanggal) == int(bln))
    else:  # rentang tanggal
        try:
            d_dari = datetime.strptime(tgl_dari, '%Y-%m-%d').date() if tgl_dari else datetime.now().date()
            d_sampai = datetime.strptime(tgl_sampai, '%Y-%m-%d').date() if tgl_sampai else datetime.now().date()
        except ValueError:
            d_dari = d_sampai = datetime.now().date()
        pj_query = Penjualan.query.filter(func.date(Penjualan.tanggal) >= d_dari, func.date(Penjualan.tanggal) <= d_sampai)
        pb_query = Pembelian.query.filter(func.date(Pembelian.tanggal) >= d_dari, func.date(Pembelian.tanggal) <= d_sampai)
        sv_query = Service.query.filter(func.date(Service.tanggal) >= d_dari, func.date(Service.tanggal) <= d_sampai)
        km_query = KasMasuk.query.filter(func.date(KasMasuk.tanggal) >= d_dari, func.date(KasMasuk.tanggal) <= d_sampai)
        kk_query = KasKeluar.query.filter(func.date(KasKeluar.tanggal) >= d_dari, func.date(KasKeluar.tanggal) <= d_sampai)

    if cabang_id:
        pj_query = pj_query.filter_by(cabang_id=cabang_id)
        pb_query = pb_query.filter_by(cabang_id=cabang_id)
        sv_query = sv_query.filter_by(cabang_id=cabang_id)
        km_query = km_query.filter_by(cabang_id=cabang_id)
        kk_query = kk_query.filter_by(cabang_id=cabang_id)
        stok_query = Sparepart.query.filter_by(cabang_id=cabang_id)
    else:
        stok_query = Sparepart.query

    data['penjualan'] = pj_query.all()
    data['pembelian'] = pb_query.all()
    data['service'] = sv_query.all()
    data['kas_masuk'] = km_query.all()
    data['kas_keluar'] = kk_query.all()
    data['stok'] = stok_query.all()

    # Retur dalam periode yang sama
    if tipe == 'harian':
        rt_query = Retur.query.filter(func.date(Retur.tanggal) == tgl)
    elif tipe == 'bulanan':
        rt_query = Retur.query.filter(func.extract('year', Retur.tanggal) == int(tahun), func.extract('month', Retur.tanggal) == int(bln))
    else:
        rt_query = Retur.query.filter(func.date(Retur.tanggal) >= d_dari, func.date(Retur.tanggal) <= d_sampai)
    if cabang_id:
        rt_query = rt_query.filter_by(cabang_id=cabang_id)
    data['retur'] = rt_query.all()

    # --- LOGIKA SUMMARY LAPORAN ---
    # Service: hanya yang status 'sudah diambil' (sudah selesai dan dibayar)
    service_selesai = [s for s in data['service'] if s.status == 'sudah diambil']

    # Retur pelanggan: kurangi dari omzet & laba penjualan
    total_retur_pelanggan = sum(r.nominal for r in data['retur'] if r.tipe == 'pelanggan')

    omzet_penjualan_langsung = max(0, sum(p.total for p in data['penjualan']) - total_retur_pelanggan)
    omzet_sparepart_service = sum(s.total_sparepart for s in service_selesai)
    summary['omzet_sparepart'] = omzet_penjualan_langsung + omzet_sparepart_service
    summary['omzet_jasa'] = sum(s.total_jasa for s in service_selesai)
    summary['total_omzet'] = summary['omzet_sparepart'] + summary['omzet_jasa']

    laba_penjualan_langsung = sum(p.laba for p in data['penjualan'])
    # Kurangi laba dari retur: estimasi laba retur = nominal retur - (harga_beli / harga_jual * nominal)
    # Pendekatan sederhana: retur mengurangi laba sebesar nominal retur pelanggan dikurangi estimasi modal
    laba_retur_pelanggan = sum(r.nominal for r in data['retur'] if r.tipe == 'pelanggan')
    laba_penjualan_bersih = max(0, laba_penjualan_langsung - laba_retur_pelanggan)
    laba_sparepart_service = sum(s.laba_sparepart for s in service_selesai)
    summary['laba_sparepart'] = laba_penjualan_bersih + laba_sparepart_service
    summary['laba_jasa'] = sum(s.laba_jasa for s in service_selesai)
    summary['total_laba'] = summary['laba_sparepart'] + summary['laba_jasa']
    summary['total_retur_pelanggan'] = total_retur_pelanggan

    summary['modal_sparepart'] = summary['omzet_sparepart'] - summary['laba_sparepart']
    summary['modal_jasa'] = 0
    summary['total_modal'] = summary['modal_sparepart'] + summary['modal_jasa']

    summary['kas_masuk'] = sum(k.nominal for k in data['kas_masuk'])
    summary['kas_keluar'] = sum(k.nominal for k in data['kas_keluar'])
    summary['saldo_kas'] = summary['kas_masuk'] - summary['kas_keluar']

    summary['total_pembelian'] = sum(p.total for p in data['pembelian'])
    summary['hutang_pembelian'] = sum(p.sisa for p in data['pembelian'])

    # Hutang keseluruhan (semua waktu)
    q_hutang_total = db.session.query(func.sum(Pembelian.sisa)).filter(
        Pembelian.status_pembayaran == 'belum lunas', Pembelian.sisa > 0)
    if cabang_id:
        q_hutang_total = q_hutang_total.filter(Pembelian.cabang_id == cabang_id)
    summary['total_hutang_outstanding'] = q_hutang_total.scalar() or 0

    # --- DATA DIAGRAM PERBULAN (Tren Omzet & Laba Sepanjang Tahun) ---
    tahun_chart = request.args.get('tahun_chart', str(datetime.now().year))
    chart_omzet = [0] * 12
    chart_laba = [0] * 12

    pj_chart_q = db.session.query(
        func.extract('month', Penjualan.tanggal),
        func.sum(Penjualan.total),
        func.sum(Penjualan.laba)
    ).filter(func.extract('year', Penjualan.tanggal) == int(tahun_chart))

    sv_chart_q = db.session.query(
        func.extract('month', Service.tanggal),
        func.sum(Service.total),
        func.sum(Service.laba_sparepart + Service.laba_jasa)
    ).filter(func.extract('year', Service.tanggal) == int(tahun_chart), Service.status == 'sudah diambil')

    if cabang_id:
        pj_chart_q = pj_chart_q.filter(Penjualan.cabang_id == cabang_id)
        sv_chart_q = sv_chart_q.filter(Service.cabang_id == cabang_id)

    for bln_num, omzet, laba in pj_chart_q.group_by(func.extract('month', Penjualan.tanggal)).all():
        idx = int(bln_num) - 1
        chart_omzet[idx] += (omzet or 0)
        chart_laba[idx] += (laba or 0)

    for bln_num, omzet, laba in sv_chart_q.group_by(func.extract('month', Service.tanggal)).all():
        idx = int(bln_num) - 1
        chart_omzet[idx] += (omzet or 0)
        chart_laba[idx] += (laba or 0)

    tahun_sekarang = datetime.now().year
    tahun_list = list(range(tahun_sekarang - 3, tahun_sekarang + 1))

    return render_template('laporan.html', cabangs=cabangs, cabang_id=cabang_id, tipe=tipe,
                         tanggal=tanggal, bulan=bulan, tgl_dari=tgl_dari, tgl_sampai=tgl_sampai,
                         data=data, summary=summary,
                         chart_omzet=chart_omzet, chart_laba=chart_laba,
                         tahun_chart=int(tahun_chart), tahun_list=tahun_list)



@app.route('/api/sparepart/<int:cabang_id>')
@login_required
def api_sparepart(cabang_id):
    sps = Sparepart.query.filter_by(cabang_id=cabang_id).filter(Sparepart.stok > 0).all()
    return jsonify([{'id': s.id, 'nama': s.nama, 'kode': s.kode, 'harga': s.harga_jual, 'harga_beli': s.harga_beli, 'harga_jual': s.harga_jual, 'harga_jual_2': s.harga_jual_2, 'harga_jual_3': s.harga_jual_3, 'stok': s.stok} for s in sps])

@app.route('/api/scan-sparepart/<int:cabang_id>/<code>')
@login_required
def api_scan_sparepart(cabang_id, code):
    sp = Sparepart.query.filter_by(cabang_id=cabang_id, kode=code).first()
    if sp:
        return jsonify({
            'success': True,
            'data': {
                'id': sp.id,
                'nama': sp.nama,
                'kode': sp.kode,
                'harga': sp.harga_jual,
                'harga_beli': sp.harga_beli,
                'harga_jual': sp.harga_jual,
                'harga_jual_2': sp.harga_jual_2,
                'harga_jual_3': sp.harga_jual_3,
                'stok': sp.stok
            }
        })
    return jsonify({'success': False, 'message': 'Barang tidak ditemukan'})

@app.route('/wallet')
@login_required
def wallet():
    cabang_id = get_user_cabang_id()
    query = Wallet.query
    if current_user.role != 'admin':
        query = query.filter_by(cabang_id=current_user.cabang_id)
    elif cabang_id:
        query = query.filter_by(cabang_id=cabang_id)
    
    wallets = query.all()
    cabangs = Cabang.query.filter_by(aktif=True).all() if current_user.role == 'admin' else[]
    return render_template('wallet.html', wallets=wallets, cabangs=cabangs, cabang_id=cabang_id)

@app.route('/wallet/tambah', methods=['POST'])
@login_required
def wallet_tambah():
    if current_user.role == 'kasir':
        flash('Akses ditolak! Kasir tidak diizinkan menambah jenis wallet baru.', 'error')
        return redirect(url_for('wallet'))
    
    nama = request.form.get('nama')
    stok = float(request.form.get('stok', 0))
    admin = float(request.form.get('biaya_admin', 0))
    cabang_id = get_default_cabang_id()
    
    new_wallet = Wallet(nama=nama, stok_saldo=stok, biaya_admin_default=admin, cabang_id=cabang_id)
    db.session.add(new_wallet)
    db.session.commit()
    flash('Master Wallet berhasil ditambahkan!', 'success')
    return redirect(url_for('wallet'))

@app.route('/wallet/update-stok/<int:id>', methods=['POST'])
@login_required
def wallet_update_stok(id):
    w = Wallet.query.get_or_404(id)
    
    if current_user.role != 'admin' and w.cabang_id != current_user.cabang_id:
        flash('Akses ditolak! Wallet ini milik cabang lain.', 'error')
        return redirect(url_for('wallet'))

    tambah_stok = float(request.form.get('tambah_stok', 0))
    w.stok_saldo += tambah_stok
    
    kas = KasKeluar(keperluan=f'Isi Saldo {w.nama}', nominal=tambah_stok, 
                  keterangan=f'Update stok oleh {current_user.nama_lengkap}',
                  cabang_id=w.cabang_id, user_id=current_user.id)
    db.session.add(kas)
    db.session.commit()
    
    flash(f'Stok saldo {w.nama} berhasil diperbarui!', 'success')
    return redirect(url_for('wallet'))

@app.route('/wallet/edit/<int:id>', methods=['POST'])
@login_required
def wallet_edit(id):
    if current_user.role == 'kasir':
        flash('Akses ditolak! Kasir tidak diizinkan mengedit data wallet.', 'error')
        return redirect(url_for('wallet'))

    w = Wallet.query.get_or_404(id)
    if current_user.role != 'admin' and w.cabang_id != current_user.cabang_id:
        flash('Akses ditolak!', 'error')
        return redirect(url_for('wallet'))

    w.nama = request.form.get('nama')
    w.biaya_admin_default = float(request.form.get('biaya_admin', 0))

    saldo_baru = float(request.form.get('saldo_baru', w.stok_saldo))
    selisih = saldo_baru - w.stok_saldo

    if selisih > 0:
        kas = KasKeluar(keperluan=f'Koreksi Saldo {w.nama}', nominal=selisih,
                        keterangan=f'Koreksi tambah saldo oleh {current_user.nama_lengkap}',
                        cabang_id=w.cabang_id, user_id=current_user.id)
        db.session.add(kas)
    elif selisih < 0:
        kas = KasMasuk(sumber='manual', nominal=abs(selisih),
                       keterangan=f'Koreksi kurang saldo {w.nama} oleh {current_user.nama_lengkap}',
                       cabang_id=w.cabang_id, user_id=current_user.id)
        db.session.add(kas)

    w.stok_saldo = saldo_baru
    db.session.commit()
    flash(f'Wallet {w.nama} berhasil diupdate!', 'success')
    return redirect(url_for('wallet'))


@app.route('/wallet/hapus/<int:id>')
@login_required
@admin_required
def wallet_hapus(id):
    w = Wallet.query.get_or_404(id)
    if TopUp.query.filter_by(wallet_id=id).first():
        flash(f'Gagal! Wallet "{w.nama}" tidak bisa dihapus karena masih memiliki riwayat transaksi Top Up.', 'error')
        return redirect(url_for('wallet'))
    nama = w.nama
    db.session.delete(w)
    db.session.commit()
    flash(f'Wallet "{nama}" berhasil dihapus!', 'success')
    return redirect(url_for('wallet'))


@app.route('/piutang')
@login_required
def piutang():
    search = request.args.get('search', '')
    cabang_id = get_user_cabang_id()

    pj_query = Penjualan.query.filter(Penjualan.status_pembayaran == 'kasbon', Penjualan.sisa > 0)
    sv_query = Service.query.filter(Service.status_pembayaran == 'belum lunas', Service.sisa > 0)
    # Service yang belum selesai (belum diambil) dianggap piutang: uang belum diterima sama sekali
    sv_proses_query = Service.query.filter(Service.status.in_(['proses pengerjaan', 'sudah selesai dikerjakan']))

    if current_user.role != 'admin':
        pj_query = pj_query.filter_by(cabang_id=current_user.cabang_id)
        sv_query = sv_query.filter_by(cabang_id=current_user.cabang_id)
        sv_proses_query = sv_proses_query.filter_by(cabang_id=current_user.cabang_id)
    elif cabang_id:
        pj_query = pj_query.filter_by(cabang_id=cabang_id)
        sv_query = sv_query.filter_by(cabang_id=cabang_id)
        sv_proses_query = sv_proses_query.filter_by(cabang_id=cabang_id)

    if search:
        pj_query = pj_query.filter(Penjualan.no_nota.ilike(f'%{search}%') | Penjualan.pelanggan.ilike(f'%{search}%'))
        sv_query = sv_query.filter(Service.no_nota.ilike(f'%{search}%') | Service.pelanggan.ilike(f'%{search}%'))
        sv_proses_query = sv_proses_query.filter(Service.no_nota.ilike(f'%{search}%') | Service.pelanggan.ilike(f'%{search}%'))

    items = []
    for pj in pj_query.order_by(Penjualan.tanggal.desc()).all():
        pj.tipe = 'penjualan'
        items.append(pj)
    for sv in sv_query.order_by(Service.tanggal.desc()).all():
        sv.tipe = 'service'
        items.append(sv)
    for sv in sv_proses_query.order_by(Service.tanggal.desc()).all():
        sv.tipe = 'service_proses'
        sv.sisa = sv.total - (sv.dibayar or 0)  # belum ada pembayaran diterima sama sekali
        items.append(sv)

    items.sort(key=lambda x: x.tanggal, reverse=True)

    from types import SimpleNamespace
    pagination = SimpleNamespace(items=items)
    cabangs = Cabang.query.filter_by(aktif=True).all() if current_user.role == 'admin' else []

    return render_template('piutang.html', pagination=pagination, search=search, cabangs=cabangs, cabang_id=cabang_id)

@app.route('/piutang/bayar/<tipe>/<int:id>', methods=['POST'])
@login_required
def piutang_bayar(tipe, id):
    nominal_bayar = float(request.form.get('nominal', 0))

    if nominal_bayar <= 0:
        flash('Nominal bayar tidak valid!', 'error')
        return redirect(url_for('piutang'))

    if tipe == 'service':
        sv = Service.query.get_or_404(id)
        if nominal_bayar > sv.sisa:
            nominal_bayar = sv.sisa

        history = PembayaranServicePiutang(service_id=sv.id, nominal=nominal_bayar, user_id=current_user.id)
        db.session.add(history)

        sv.dibayar += nominal_bayar
        sv.sisa -= nominal_bayar
        if sv.sisa <= 0:
            sv.status_pembayaran = 'lunas'

        kas = KasMasuk(sumber='piutang_service', referensi_id=sv.id, nominal=nominal_bayar,
                      keterangan=f'Pembayaran Piutang Service {sv.no_nota}',
                      cabang_id=sv.cabang_id, user_id=current_user.id)
        db.session.add(kas)
        db.session.commit()
        flash(f'Pembayaran cicilan untuk {sv.no_nota} berhasil!', 'success')
    else:
        pj = Penjualan.query.get_or_404(id)
        if nominal_bayar > pj.sisa:
            nominal_bayar = pj.sisa  # Cegah bayar lebih dari sisa

        # Catat history pembayaran
        history = PembayaranPiutang(penjualan_id=pj.id, nominal=nominal_bayar, user_id=current_user.id)
        db.session.add(history)

        # Update sisa dan dibayar di Penjualan
        pj.dibayar += nominal_bayar
        pj.sisa -= nominal_bayar
        if pj.sisa <= 0:
            pj.status_pembayaran = 'lunas'

        # Tambah Kas Masuk
        kas = KasMasuk(sumber='piutang', referensi_id=pj.id, nominal=nominal_bayar,
                      keterangan=f'Pembayaran Piutang Nota {pj.no_nota}',
                      cabang_id=pj.cabang_id, user_id=current_user.id)
        db.session.add(kas)

        db.session.commit()
        flash(f'Pembayaran cicilan untuk {pj.no_nota} berhasil!', 'success')

    return redirect(url_for('piutang'))

@app.route('/piutang/history/<tipe>/<int:id>')
@login_required
def piutang_history(tipe, id):
    if tipe == 'service':
        history = PembayaranServicePiutang.query.filter_by(service_id=id).order_by(PembayaranServicePiutang.tanggal.asc()).all()
    else:
        history = PembayaranPiutang.query.filter_by(penjualan_id=id).order_by(PembayaranPiutang.tanggal.asc()).all()
    data = [{
        'tanggal': h.tanggal.strftime('%d/%m/%Y %H:%M'),
        'nominal': h.nominal,
        'penerima': h.user.nama_lengkap
    } for h in history]
    return jsonify(data)

@app.route('/pembelian/cetak/<int:id>')
@login_required
def pembelian_cetak(id):
    pb = Pembelian.query.get_or_404(id)
    if current_user.role != 'admin' and pb.cabang_id != current_user.cabang_id:
        return "Akses Ditolak", 403
    subtotal = sum(item.subtotal for item in pb.items)
    return render_template('struk_pembelian.html', pb=pb, subtotal=subtotal)

@app.route('/penjualan/cetak/<int:id>')
@login_required
def penjualan_cetak(id):
    pj = Penjualan.query.get_or_404(id)
    
    # Validasi Akses
    if current_user.role != 'admin' and pj.cabang_id != current_user.cabang_id:
        return "Akses Ditolak", 403
        
    # Hitung subtotal kotor (sebelum diskon)
    subtotal = sum(item.subtotal for item in pj.items)
    
    return render_template('struk.html', pj=pj, subtotal=subtotal)

@app.route('/service/cetak/<int:id>')
@login_required
def service_cetak(id):
    sv = Service.query.get_or_404(id)
    
    # Validasi Akses
    if current_user.role != 'admin' and sv.cabang_id != current_user.cabang_id:
        return "Akses Ditolak", 403
        
    return render_template('struk_service.html', sv=sv)

@app.route('/laporan/export-csv')
@login_required
def laporan_export_csv():
    cabang_id = get_user_cabang_id()
    tipe = request.args.get('tipe', 'harian')
    tanggal = request.args.get('tanggal', datetime.now().strftime('%Y-%m-%d'))
    bulan = request.args.get('bulan', datetime.now().strftime('%Y-%m'))
    tgl_dari = request.args.get('tgl_dari', '')
    tgl_sampai = request.args.get('tgl_sampai', '')
    
    if current_user.role != 'admin':
        cabang_id = current_user.cabang_id

    # 1. Ambil Nama Cabang untuk Header
    nama_cabang = "Semua Cabang"
    if cabang_id:
        c = Cabang.query.get(cabang_id)
        if c: nama_cabang = c.nama

    # 2. Query Data
    if tipe == 'harian':
        tgl = datetime.strptime(tanggal, '%Y-%m-%d').date()
        pj_query = Penjualan.query.filter(func.date(Penjualan.tanggal) == tgl)
        pb_query = Pembelian.query.filter(func.date(Pembelian.tanggal) == tgl)
        sv_query = Service.query.filter(func.date(Service.tanggal) == tgl)
        km_query = KasMasuk.query.filter(func.date(KasMasuk.tanggal) == tgl)
        kk_query = KasKeluar.query.filter(func.date(KasKeluar.tanggal) == tgl)
        periode_str = tgl.strftime('%d %B %Y')
    elif tipe == 'bulanan':
        tahun, bln = bulan.split('-')
        pj_query = Penjualan.query.filter(func.extract('year', Penjualan.tanggal) == int(tahun), func.extract('month', Penjualan.tanggal) == int(bln))
        pb_query = Pembelian.query.filter(func.extract('year', Pembelian.tanggal) == int(tahun), func.extract('month', Pembelian.tanggal) == int(bln))
        sv_query = Service.query.filter(func.extract('year', Service.tanggal) == int(tahun), func.extract('month', Service.tanggal) == int(bln))
        km_query = KasMasuk.query.filter(func.extract('year', KasMasuk.tanggal) == int(tahun), func.extract('month', KasMasuk.tanggal) == int(bln))
        kk_query = KasKeluar.query.filter(func.extract('year', KasKeluar.tanggal) == int(tahun), func.extract('month', KasKeluar.tanggal) == int(bln))
        periode_str = f"Bulan {bln} Tahun {tahun}"
    else:
        try:
            d_dari = datetime.strptime(tgl_dari, '%Y-%m-%d').date() if tgl_dari else datetime.now().date()
            d_sampai = datetime.strptime(tgl_sampai, '%Y-%m-%d').date() if tgl_sampai else datetime.now().date()
        except ValueError:
            d_dari = d_sampai = datetime.now().date()
            
        pj_query = Penjualan.query.filter(func.date(Penjualan.tanggal) >= d_dari, func.date(Penjualan.tanggal) <= d_sampai)
        pb_query = Pembelian.query.filter(func.date(Pembelian.tanggal) >= d_dari, func.date(Pembelian.tanggal) <= d_sampai)
        sv_query = Service.query.filter(func.date(Service.tanggal) >= d_dari, func.date(Service.tanggal) <= d_sampai)
        km_query = KasMasuk.query.filter(func.date(KasMasuk.tanggal) >= d_dari, func.date(KasMasuk.tanggal) <= d_sampai)
        kk_query = KasKeluar.query.filter(func.date(KasKeluar.tanggal) >= d_dari, func.date(KasKeluar.tanggal) <= d_sampai)
        periode_str = f"Rentang {d_dari.strftime('%d %B %Y')} s/d {d_sampai.strftime('%d %B %Y')}"
    
    if cabang_id:
        pj_query = pj_query.filter_by(cabang_id=cabang_id)
        pb_query = pb_query.filter_by(cabang_id=cabang_id)
        sv_query = sv_query.filter_by(cabang_id=cabang_id)
        km_query = km_query.filter_by(cabang_id=cabang_id)
        kk_query = kk_query.filter_by(cabang_id=cabang_id)
        stok_query = Sparepart.query.filter_by(cabang_id=cabang_id)
    else:
        stok_query = Sparepart.query

    data_pj = pj_query.all()
    data_sv = sv_query.all()
    data_km = km_query.all()
    data_kk = kk_query.all()
    data_stok = stok_query.all()

    # 3. Kalkulasi Summary
    omzet_sp = sum(p.total for p in data_pj) + sum(s.total_sparepart for s in data_sv)
    omzet_jasa = sum(s.total_jasa for s in data_sv)
    
    laba_sp = sum(p.laba for p in data_pj) + sum(s.laba_sparepart for s in data_sv)
    laba_jasa = sum(s.laba_jasa for s in data_sv)

    modal_sp = omzet_sp - laba_sp

    kas_masuk = sum(k.nominal for k in data_km)
    kas_keluar = sum(k.nominal for k in data_kk)

    # 4. Generate CSV
    output = io.StringIO()
    
    # TRIK KHUSUS EXCEL: Memaksa Excel membaca koma sebagai pemisah kolom
    output.write("sep=,\n")
    
    # Menggunakan delimiter koma standar
    writer = csv.writer(output, delimiter=',', lineterminator='\n')

    # --- SECTION: HEADER ---
    writer.writerow(["LAPORAN KEUANGAN - F1 CELL SERVICE HP"])
    writer.writerow(["Cabang", nama_cabang])
    writer.writerow(["Periode", periode_str])
    writer.writerow(["Tgl Cetak", datetime.now().strftime('%d/%m/%Y %H:%M')])
    writer.writerow([])

    # --- SECTION: LABA RUGI (disembunyikan untuk role kasir) ---
    if current_user.role != 'kasir':
        writer.writerow(["RINGKASAN LABA RUGI", "OMZET", "MODAL (HPP)", "LABA BERSIH"])
        writer.writerow(["Penjualan Sparepart", int(omzet_sp), int(modal_sp), int(laba_sp)])
        writer.writerow(["Jasa Service", int(omzet_jasa), 0, int(laba_jasa)])
        writer.writerow(["TOTAL KESELURUHAN", int(omzet_sp+omzet_jasa), int(modal_sp), int(laba_sp+laba_jasa)])
        writer.writerow([])

    # --- SECTION: ARUS KAS ---
    writer.writerow(["RINGKASAN ARUS KAS", "NOMINAL"])
    writer.writerow(["Total Kas Masuk", int(kas_masuk)])
    writer.writerow(["Total Kas Keluar", int(kas_keluar)])
    writer.writerow(["SALDO KAS BERSIH", int(kas_masuk - kas_keluar)])
    writer.writerow([])

# --- SECTION: DETAIL TRANSAKSI ---
    writer.writerow(["RINCIAN TRANSAKSI PENJUALAN & SERVICE"])
    if current_user.role != 'kasir':
        writer.writerow(["Tanggal", "Tipe", "No Nota", "Pelanggan/Keterangan", "Total Omzet", "Modal (HPP)", "Total Laba"])

        for p in data_pj:
            modal_pj = p.total - p.laba
            writer.writerow([p.tanggal.strftime('%Y-%m-%d %H:%M'), "Penjualan", p.no_nota, p.pelanggan or 'Umum', int(p.total), int(modal_pj), int(p.laba)])

        for s in data_sv:
            modal_sv = (s.total_sparepart or 0) - (s.laba_sparepart or 0)
            writer.writerow([s.tanggal.strftime('%Y-%m-%d %H:%M'), "Service", s.no_nota, s.pelanggan, int(s.total), int(modal_sv), int(s.laba_jasa + s.laba_sparepart)])

        if not data_pj and not data_sv:
            writer.writerow(["-", "Tidak ada transaksi", "-", "-", "0", "0", "0"])
    else:
        writer.writerow(["Tanggal", "Tipe", "No Nota", "Pelanggan/Keterangan", "Total Omzet"])

        for p in data_pj:
            writer.writerow([p.tanggal.strftime('%Y-%m-%d %H:%M'), "Penjualan", p.no_nota, p.pelanggan or 'Umum', int(p.total)])

        for s in data_sv:
            writer.writerow([s.tanggal.strftime('%Y-%m-%d %H:%M'), "Service", s.no_nota, s.pelanggan, int(s.total)])

        if not data_pj and not data_sv:
            writer.writerow(["-", "Tidak ada transaksi", "-", "-", "0"])
    writer.writerow([])

    # --- SECTION: VALUASI STOK (disembunyikan untuk role kasir, berisi harga modal) ---
    if current_user.role != 'kasir':
        writer.writerow(["VALUASI ASET STOK SPAREPART"])
        writer.writerow(["Kode", "Nama Sparepart", "Stok Sisa", "Harga Modal Satuan", "Total Nilai Aset"])

        total_aset = 0
        for s in data_stok:
            aset = s.stok * s.harga_beli
            total_aset += aset
            writer.writerow([s.kode, s.nama, s.stok, int(s.harga_beli), int(aset)])

        writer.writerow(["", "", "", "TOTAL VALUASI", int(total_aset)])

    # 5. Return Response Download
    # Tambahkan UTF-8 dengan BOM agar Excel tidak salah baca karakter aneh
    response = Response(output.getvalue().encode('utf-8-sig'), mimetype="text/csv")
    filename = f"Laporan_Keuangan_{tipe}_{tanggal if tipe=='harian' else bulan}.csv"
    response.headers["Content-Disposition"] = f"attachment; filename={filename}"
    return response