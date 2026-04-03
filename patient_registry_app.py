import os
from datetime import datetime
from functools import wraps
from flask import Flask, request, redirect, url_for, render_template_string, flash, session, send_from_directory
try:
    import cloudinary
    import cloudinary.uploader
except ImportError:
    cloudinary = None
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'change-this-secret-key')

# Database setup
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///patients.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'pdf', 'dcm', 'dicom'}
FILE_CATEGORY_VALUES = ['Rentgen', 'MRT', 'Digər sənəd']
STATUS_VALUES = ['Aktiv', 'Kontrol', 'Bitmiş', 'Arxiv']
SOURCE_VALUES = ['Instagram', 'TikTok', 'Google', 'Digər']

# Cloudinary
CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME', '').strip()
CLOUDINARY_API_KEY = os.environ.get('CLOUDINARY_API_KEY', '').strip()
CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET', '').strip()

CLOUDINARY_ENABLED = bool(
    cloudinary and CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET
)

if CLOUDINARY_ENABLED:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True,
    )

db = SQLAlchemy(app)

APP_USERNAME = os.environ.get('APP_USERNAME', 'admin')
APP_PASSWORD = os.environ.get('APP_PASSWORD', '1234')

# ====================== MODELLER ======================
class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(200), nullable=False, index=True)
    age = db.Column(db.Integer, nullable=True)
    gender = db.Column(db.String(30), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    address = db.Column(db.String(300), nullable=True)
    referral_source = db.Column(db.String(100), nullable=True)
    referral_source_other = db.Column(db.String(150), nullable=True)
    admission_date = db.Column(db.Date, nullable=True)
    complaint = db.Column(db.Text, nullable=True)
    complaint_duration = db.Column(db.String(150), nullable=True)
    diagnosis = db.Column(db.Text, nullable=True)
    operation_treatment_history = db.Column(db.Text, nullable=True)
    past_diseases_traumas = db.Column(db.Text, nullable=True)
    treatment_plan = db.Column(db.Text, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(30), default='Aktiv', nullable=False, index=True)
    is_deleted = db.Column(db.Boolean, default=False, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    sessions = db.relationship('SessionNote', backref='patient', lazy=True, cascade='all, delete-orphan')
    files = db.relationship('PatientFile', backref='patient', lazy=True, cascade='all, delete-orphan')


class SessionNote(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False, index=True)
    session_date = db.Column(db.Date, nullable=False, default=datetime.utcnow().date)
    session_type = db.Column(db.String(100), nullable=True)
    status = db.Column(db.String(30), nullable=True)
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class PatientFile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    patient_id = db.Column(db.Integer, db.ForeignKey('patient.id'), nullable=False, index=True)
    category = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(200), nullable=True)
    original_filename = db.Column(db.String(255), nullable=False)
    stored_filename = db.Column(db.String(255), nullable=False, unique=True)
    file_ext = db.Column(db.String(20), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


with app.app_context():
    db.create_all()


# ====================== HELPER FUNKSIYALAR ======================
def login_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login', next=request.path))
        return view_func(*args, **kwargs)
    return wrapper


def parse_date(value: str):
    value = (value or '').strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, '%Y-%m-%d').date()
    except ValueError:
        return None


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def get_file_resource_type(file_ext: str) -> str:
    return 'image' if file_ext in {'jpg', 'jpeg', 'png', 'webp'} else 'raw'


def normalize_uploaded_files(*field_names: str):
    files = []
    for field_name in field_names:
        for item in request.files.getlist(field_name):
            if item and item.filename:
                files.append(item)
    return files


def save_uploaded_files(patient_id: int, uploaded_files, category: str, title: str):
    saved_count = 0
    for uploaded in uploaded_files:
        original_filename = uploaded.filename or ''
        if not original_filename or not allowed_file(original_filename):
            continue

        safe_name = secure_filename(original_filename)
        if '.' not in safe_name:
            continue

        ext = safe_name.rsplit('.', 1)[1].lower()

        if CLOUDINARY_ENABLED:
            resource_type = get_file_resource_type(ext)
            upload_result = cloudinary.uploader.upload(
                uploaded,
                resource_type=resource_type,
                folder=f'patient_files/{patient_id}',
                unique_filename=True,
                overwrite=False,
            )
            stored_filename = upload_result.get('public_id') or ''
        else:
            timestamp = int(datetime.utcnow().timestamp() * 1000)
            stored_filename = f"{patient_id}_{timestamp}_{safe_name}"
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], stored_filename)
            uploaded.save(save_path)

        patient_file = PatientFile(
            patient_id=patient_id,
            category=category or 'Digər sənəd',
            title=title or None,
            original_filename=original_filename,
            stored_filename=stored_filename,
            file_ext=ext,
        )
        db.session.add(patient_file)
        saved_count += 1

    return saved_count


def delete_uploaded_file(patient_file):
    """Faylı həm Cloudinary-dən, həm də lokal diskdən silir"""
    if not patient_file or not patient_file.stored_filename:
        return

    if CLOUDINARY_ENABLED:
        resource_type = get_file_resource_type((patient_file.file_ext or '').lower())
        try:
            cloudinary.uploader.destroy(
                patient_file.stored_filename,
                resource_type=resource_type,
                invalidate=True,
            )
        except Exception as e:
            print(f"Cloudinary delete error: {e}")
        return

    # Lokal fayl silinməsi
    file_path = os.path.join(app.config['UPLOAD_FOLDER'], patient_file.stored_filename)
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except Exception as e:
            print(f"Local file delete error: {e}")


def get_patient_file_url(patient_file):
    if not patient_file:
        return None

    if CLOUDINARY_ENABLED:
        resource_type = get_file_resource_type((patient_file.file_ext or '').lower())
        try:
            return cloudinary.utils.cloudinary_url(
                patient_file.stored_filename,
                resource_type=resource_type,
                secure=True,
            )[0]
        except:
            pass

    # Lokal fayl
    return url_for('serve_patient_file', filename=patient_file.stored_filename)


# ====================== HTML TEMPLATES ======================
# (BASE_HTML, LOGIN_BODY, INDEX_BODY, PATIENT_FORM_BODY, VIEW_BODY, SESSION_FORM_BODY)
# ... Bunlar əvvəlki kodda olduğu kimi qalır. Sadəcə yer qənaəti üçün burada yazmıram.
# Əgər istəsən, onları da ayrıca göndərə bilərəm.

# ====================== ROUTES ======================
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        if username == APP_USERNAME and password == APP_PASSWORD:
            session['logged_in'] = True
            session['username'] = username
            flash('Giriş uğurludur.')
            next_url = request.args.get('next') or url_for('index')
            return redirect(next_url)
        flash('İstifadəçi adı və ya şifrə yanlışdır.')
    return render_page('Giriş', LOGIN_BODY)


@app.route('/logout')
def logout():
    session.clear()
    flash('Çıxış edildi.')
    return redirect(url_for('login'))


# Digər bütün route-lar (index, new_patient, view_patient, edit_patient, new_session, upload_patient_file, delete_patient_file və s.) 
# əvvəlki mesajda verdiyim düzəlişlərlə eynidir.

# Ən vacib düzəliş edilmiş upload route:
@app.route('/patients/<int:patient_id>/files/upload', methods=['POST'])
@login_required
def upload_patient_file(patient_id):
    patient = Patient.query.filter_by(id=patient_id, is_deleted=False).first_or_404()
    category = request.form.get('category', '').strip()
    title = request.form.get('title', '').strip()

    uploaded_files = normalize_uploaded_files('files', 'folder_files')

    if not uploaded_files:
        flash('Fayl seçilməyib.')
        return redirect(url_for('view_patient', patient_id=patient.id))

    saved_count = save_uploaded_files(patient.id, uploaded_files, category, title)
    db.session.commit()

    flash(f'{saved_count} fayl uğurla yükləndi.' if saved_count > 0 else 'Fayl yüklənmədi.')
    return redirect(url_for('view_patient', patient_id=patient.id))


@app.route('/patient-files/<int:file_id>/delete', methods=['POST'])
@login_required
def delete_patient_file(file_id):
    patient_file = PatientFile.query.get_or_404(file_id)
    patient_id = patient_file.patient_id

    delete_uploaded_file(patient_file)        # ← Düzəldilmiş funksiya
    db.session.delete(patient_file)
    db.session.commit()

    flash('Fayl silindi.')
    return redirect(url_for('view_patient', patient_id=patient_id))


@app.route('/uploads/<path:filename>')
@login_required
def serve_patient_file(filename):
    # Cloudinary istifadə olunursa redirect olunur, yoxsa lokal fayl verilir
    patient_file = PatientFile.query.filter_by(stored_filename=filename).first()
    if patient_file:
        remote_url = get_patient_file_url(patient_file)
        if remote_url and CLOUDINARY_ENABLED:
            return redirect(remote_url)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


# render_page funksiyası da əvvəlki kimi qalır...

if __name__ == '__main__':
    app.run(debug=True)
