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

# Production: set DATABASE_URL to PostgreSQL.
# Local testing: falls back to SQLite.
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith('postgres://'):
    database_url = database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///patients.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
ALLOWED_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp', 'pdf', 'dcm', 'dicom'}
FILE_CATEGORY_VALUES = ['Rentgen', 'MRT', 'Digər sənəd']
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)

APP_USERNAME = os.environ.get('APP_USERNAME', 'admin')
APP_PASSWORD = os.environ.get('APP_PASSWORD', '1234')
STATUS_VALUES = ['Aktiv', 'Kontrol', 'Bitmiş', 'Arxiv']
SOURCE_VALUES = ['Instagram', 'TikTok', 'Google', 'Digər']

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

    sessions = db.relationship(
        'SessionNote',
        backref='patient',
        lazy=True,
        cascade='all, delete-orphan',
        order_by='desc(SessionNote.session_date), desc(SessionNote.id)',
    )

    files = db.relationship(
        'PatientFile',
        backref='patient',
        lazy=True,
        cascade='all, delete-orphan',
        order_by='desc(PatientFile.created_at), desc(PatientFile.id)',
    )


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
    return datetime.strptime(value, '%Y-%m-%d').date()


def allowed_file(filename: str) -> bool:
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def normalize_uploaded_files(*field_names: str):
    files = []
    for field_name in field_names:
        for item in request.files.getlist(field_name):
            if item and item.filename:
                files.append(item)
    return files


def get_file_resource_type(file_ext: str) -> str:
    return 'image' if file_ext in {'jpg', 'jpeg', 'png', 'webp'} else 'raw'


def build_local_stored_filename(patient_id: int, safe_name: str) -> str:
    return f"{patient_id}_{int(datetime.utcnow().timestamp() * 1000)}_{safe_name}"


def get_remote_file_url(patient_file):
    if not patient_file.stored_filename:
        return None
    if patient_file.stored_filename.startswith('http://') or patient_file.stored_filename.startswith('https://'):
        return patient_file.stored_filename
    if CLOUDINARY_ENABLED:
        resource_type = get_file_resource_type((patient_file.file_ext or '').lower())
        return cloudinary.utils.cloudinary_url(
            patient_file.stored_filename,
            resource_type=resource_type,
            secure=True,
        )[0]
    return None


def get_patient_file_url(patient_file):
    remote_url = get_remote_file_url(patient_file)
    if remote_url:
        return remote_url
    return url_for('serve_patient_file', filename=patient_file.stored_filename)


def save_uploaded_files(patient_id: int, uploaded_files, category: str, title: str):
    saved_count = 0
    for uploaded in uploaded_files:
        original_filename = uploaded.filename or ''
        if not original_filename:
            continue
        if not allowed_file(original_filename):
            continue

        safe_name = secure_filename(original_filename)
        if not safe_name or '.' not in safe_name:
            continue
        ext = safe_name.rsplit('.', 1)[1].lower()
        resource_type = get_file_resource_type(ext)

        if CLOUDINARY_ENABLED:
            upload_result = cloudinary.uploader.upload(
                uploaded,
                resource_type=resource_type,
                folder=f'patient_files/{patient_id}',
                public_id=os.path.splitext(safe_name)[0],
                unique_filename=True,
                overwrite=False,
                use_filename=True,
            )
            stored_filename = upload_result.get('public_id') or ''
        else:
            stored_filename = build_local_stored_filename(patient_id, safe_name)
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], stored_filename)
            uploaded.save(save_path)

        patient_file = PatientFile(
            patient_id=patient_id,
            category=category or 'Digər sənəd',
            title=title,
            original_filename=original_filename,
            stored_filename=stored_filename,
            file_ext=ext,
        )
        db.session.add(patient_file)
        saved_count += 1
    return saved_count


def delete_uploaded_file(patient_file):
    if not patient_file.stored_filename:
        return

    remote_url = get_remote_file_url(patient_file)
    if remote_url and CLOUDINARY_ENABLED:
        resource_type = get_file_resource_type((patient_file.file_ext or '').lower())
        cloudinary.uploader.destroy(
            patient_file.stored_filename,
            resource_type=resource_type,
            invalidate=True,
        )
        return

    delete_uploaded_file(patient_file)


BASE_HTML = """
<!doctype html>
<html lang="az">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>
  <style>
    :root {
      --bg: #f6f7fb;
      --card: #ffffff;
      --text: #1e2430;
      --muted: #6a7381;
      --line: #e4e8f0;
      --accent: #1f6feb;
      --accent-soft: #eef4ff;
      --danger: #c62828;
      --success: #157347;
      --warning: #b26a00;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Arial, Helvetica, sans-serif;
      background: var(--bg);
      color: var(--text);
    }
    .wrap {
      max-width: 1080px;
      margin: 0 auto;
      padding: 16px;
    }
    .nav {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 16px;
    }
    .nav-left, .nav-right {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
      align-items: center;
    }
    .nav a, .btn {
      display: inline-block;
      text-decoration: none;
      background: var(--accent);
      color: white;
      padding: 11px 15px;
      border-radius: 12px;
      border: none;
      cursor: pointer;
      font-size: 14px;
    }
    .nav a.secondary, .btn.secondary {
      background: white;
      color: var(--text);
      border: 1px solid var(--line);
    }
    .btn.danger {
      background: var(--danger);
      color: white;
    }
    .btn.warning {
      background: var(--warning);
      color: white;
    }
    .card {
      background: var(--card);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 16px;
      margin-bottom: 14px;
      box-shadow: 0 3px 18px rgba(18, 32, 56, 0.05);
    }
    h1, h2, h3 { margin-top: 0; }
    .grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
    }
    .grid-3 {
      display: grid;
      grid-template-columns: 1fr 1fr 1fr;
      gap: 14px;
    }
    @media (max-width: 800px) {
      .grid, .grid-3 { grid-template-columns: 1fr; }
      .nav { align-items: flex-start; }
    }
    label {
      display: block;
      font-size: 14px;
      margin-bottom: 6px;
      color: var(--muted);
    }
    input, textarea, select {
      width: 100%;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 12px;
      font-size: 15px;
      outline: none;
      background: white;
    }
    textarea {
      min-height: 110px;
      resize: vertical;
    }
    .field { margin-bottom: 12px; }
    .patient-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }
    @media (max-width: 800px) {
      .patient-row { flex-direction: column; }
    }
    .muted {
      color: var(--muted);
      font-size: 14px;
    }
    .flash {
      background: #ecfdf3;
      color: var(--success);
      border: 1px solid #b7ebc6;
      padding: 12px;
      border-radius: 12px;
      margin-bottom: 12px;
    }
    .actions {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }
    .empty {
      text-align: center;
      padding: 26px;
      color: var(--muted);
    }
    .badge {
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 13px;
      font-weight: bold;
      margin-bottom: 10px;
    }
    .login-box {
      max-width: 420px;
      margin: 60px auto;
    }
    .small {
      font-size: 13px;
      color: var(--muted);
    }
    .session-item {
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      margin-bottom: 10px;
    }
    .top-space {
      margin-top: 14px;
    }
  </style>
</head>
<body>
  <div class="wrap">
    {% if session.get('logged_in') %}
    <div class="nav">
      <div class="nav-left">
        <a href="{{ url_for('index') }}">Ana səhifə</a>
        <a href="{{ url_for('new_patient') }}">Yeni xəstə</a>
        <a class="secondary" href="{{ url_for('index') }}">Axtarış</a>
      </div>
      <div class="nav-right">
        <span class="muted">İstifadəçi: {{ session.get('username') }}</span>
        <a class="secondary" href="{{ url_for('logout') }}">Çıxış</a>
      </div>
    </div>
    {% endif %}

    {% with messages = get_flashed_messages() %}
      {% if messages %}
        {% for message in messages %}
          <div class="flash">{{ message }}</div>
        {% endfor %}
      {% endif %}
    {% endwith %}

    {{ body|safe }}
  </div>
  <script>
    function toggleOtherSource() {
      const select = document.getElementById('referral_source');
      const field = document.getElementById('other_source_field');
      if (!select || !field) return;
      field.style.display = select.value === 'Digər' ? 'block' : 'none';
    }
    document.addEventListener('DOMContentLoaded', toggleOtherSource);
  </script>
</body>
</html>
"""


LOGIN_BODY = """
<div class="login-box card">
  <h1>Giriş</h1>
  <p class="muted">Pasiyent məlumatlarını qorumaq üçün giriş et.</p>
  <form method="post" enctype="multipart/form-data">
    <div class="field">
      <label>İstifadəçi adı</label>
      <input type="text" name="username" required>
    </div>
    <div class="field">
      <label>Şifrə</label>
      <input type="password" name="password" required>
    </div>
    <button class="btn" type="submit">Daxil ol</button>
    
  </form>
</div>
"""


INDEX_BODY = """
<div class="card">
  <h1>Xəstə bazası</h1>
  <p class="muted">Ad, telefon, şikayət, diaqnoz, mənbə və müraciət tarixinə görə axtarış edə bilərsən.</p>
  <form method="get">
    <div class="grid-3">
      <div class="field">
        <label>Axtarış</label>
        <input type="text" name="q" value="{{ q }}" placeholder="Məsələn: Murad, bel ağrısı, skolyoz">
      </div>
      <div class="field">
        <label>Status</label>
        <select name="status">
          <option value="">Hamısı</option>
          {% for item in statuses %}
            <option value="{{ item }}" {% if selected_status == item %}selected{% endif %}>{{ item }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="field">
        <label>Mənbə</label>
        <select name="source">
          <option value="">Hamısı</option>
          {% for item in sources %}
            <option value="{{ item }}" {% if selected_source == item %}selected{% endif %}>{{ item }}</option>
          {% endfor %}
        </select>
      </div>
    </div>

    <div class="grid">
      <div class="field">
        <label>Müraciət tarixi (dəqiq gün)</label>
        <input type="date" name="admission_date" value="{{ selected_admission_date }}">
      </div>
      <div class="field">
        <label>Ay üzrə axtarış</label>
        <input type="month" name="admission_month" value="{{ selected_admission_month }}">
      </div>
    </div>

    <div class="actions">
      <button class="btn" type="submit">Axtar</button>
      <a class="btn secondary" href="{{ url_for('index') }}">Sıfırla</a>
    </div>
  </form>
</div>

{% if patients %}
  {% for patient in patients %}
    <div class="card">
      <div class="patient-row">
        <div>
          <div class="badge">{{ patient.status }}</div>
          <h3>{{ patient.full_name }}</h3>
          <div class="muted">Telefon: {{ patient.phone or '-' }}</div>
          <div class="muted">Ünvan: {{ patient.address or '-' }}</div>
          <div class="muted">Yaş: {{ patient.age or '-' }} | Cins: {{ patient.gender or '-' }}</div>
          <div class="muted">Müraciət tarixi: {{ patient.admission_date.strftime('%Y-%m-%d') if patient.admission_date else '-' }}</div>
          <div class="muted">Mənbə: {{ patient.referral_source_other if patient.referral_source == 'Digər' and patient.referral_source_other else patient.referral_source or '-' }}</div>
          <p><strong>Şikayətlər və müddəti:</strong><br>{{ patient.complaint or '-' }}{% if patient.complaint_duration %}<br><span class="muted">Müddət: {{ patient.complaint_duration }}</span>{% endif %}</p>
          <p><strong>Diaqnoz:</strong><br>{{ patient.diagnosis or '-' }}</p>
        </div>
        <div class="actions">
          <a class="btn secondary" href="{{ url_for('view_patient', patient_id=patient.id) }}">Bax</a>
          <a class="btn" href="{{ url_for('edit_patient', patient_id=patient.id) }}">Düzəliş et</a>
        </div>
      </div>
    </div>
  {% endfor %}
{% else %}
  <div class="card empty">Heç bir xəstə tapılmadı.</div>
{% endif %}
"""


PATIENT_FORM_BODY = """
<div class="card">
  <h1>{{ heading }}</h1>
  <form method="post">
    <div class="grid-3">
      <div class="field">
        <label>Ad soyad</label>
        <input type="text" name="full_name" value="{{ patient.full_name if patient else '' }}" required>
      </div>
      <div class="field">
        <label>Yaş</label>
        <input type="number" name="age" value="{{ patient.age if patient and patient.age is not none else '' }}">
      </div>
      <div class="field">
        <label>Cins</label>
        <select name="gender">
          <option value="">Seç</option>
          {% for item in ['Kişi', 'Qadın', 'Uşaq'] %}
            <option value="{{ item }}" {% if patient and patient.gender == item %}selected{% endif %}>{{ item }}</option>
          {% endfor %}
        </select>
      </div>
    </div>

    <div class="grid-3">
      <div class="field">
        <label>Telefon</label>
        <input type="text" name="phone" value="{{ patient.phone if patient else '' }}">
      </div>
      <div class="field">
        <label>Ünvan</label>
        <input type="text" name="address" value="{{ patient.address if patient else '' }}">
      </div>
      <div class="field">
        <label>Müraciət tarixi</label>
        <input type="date" name="admission_date" value="{{ patient.admission_date.isoformat() if patient and patient.admission_date else '' }}">
      </div>
    </div>

    <div class="grid">
      <div class="field">
        <label>Mənbə</label>
        <select name="referral_source" id="referral_source" onchange="toggleOtherSource()">
          <option value="">Seç</option>
          {% for item in sources %}
            <option value="{{ item }}" {% if patient and patient.referral_source == item %}selected{% endif %}>{{ item }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="field" id="other_source_field" style="display: {% if patient and patient.referral_source == 'Digər' %}block{% else %}none{% endif %};">
        <label>Digər mənbənin adı</label>
        <input type="text" name="referral_source_other" value="{{ patient.referral_source_other if patient else '' }}">
      </div>
    </div>

    <div class="grid">
      <div class="field">
        <label>Şikayətlər</label>
        <textarea name="complaint">{{ patient.complaint if patient else '' }}</textarea>
      </div>
      <div class="field">
        <label>Şikayətlərin müddəti</label>
        <input type="text" name="complaint_duration" value="{{ patient.complaint_duration if patient else '' }}" placeholder="Məsələn: 3 ay, 2 həftə, 1 il">
      </div>
    </div>

    <div class="field">
      <label>Diaqnoz</label>
      <textarea name="diagnosis">{{ patient.diagnosis if patient else '' }}</textarea>
    </div>

    <div class="field">
      <label>Əməliyyat və müalicə keçmişi</label>
      <textarea name="operation_treatment_history">{{ patient.operation_treatment_history if patient else '' }}</textarea>
    </div>

    <div class="field">
      <label>Keçirilmiş xəstəliklər, travmalar</label>
      <textarea name="past_diseases_traumas">{{ patient.past_diseases_traumas if patient else '' }}</textarea>
    </div>

    <div class="field">
      <label>Müalicə planı</label>
      <textarea name="treatment_plan">{{ patient.treatment_plan if patient else '' }}</textarea>
    </div>

    <div class="field">
      <label>Ümumi qeydlər</label>
      <textarea name="notes">{{ patient.notes if patient else '' }}</textarea>
    </div>

    <div class="field">
      <label>Status</label>
      <select name="status">
        {% for item in statuses %}
          <option value="{{ item }}" {% if patient and patient.status == item %}selected{% elif not patient and item == 'Aktiv' %}selected{% endif %}>{{ item }}</option>
        {% endfor %}
      </select>
    </div>

    <div class="card" style="padding:12px; margin-top:12px;">
      <h3>İlkin görüntü / fayl</h3>
      <p class="muted">Xəstəni ilk dəfə əlavə edərkən tək fayl, çoxlu fayl və ya MRT qovluğu yükləyə bilərsən.</p>

      <div class="grid-3">
        <div class="field">
          <label>Kateqoriya</label>
          <select name="initial_file_category">
            <option value="">Seç</option>
            {% for item in file_categories %}
              <option value="{{ item }}">{{ item }}</option>
            {% endfor %}
          </select>
        </div>

        <div class="field">
          <label>Başlıq</label>
          <input type="text" name="initial_file_title" placeholder="Məsələn: Servikal MRT, Lomber rentgen">
        </div>

        <div class="field">
          <label>Çoxlu fayl seç</label>
          <input type="file" name="initial_files" accept=".jpg,.jpeg,.png,.webp,.pdf,.dcm,.dicom" multiple>
        </div>
      </div>

      <div class="field">
        <label>MRT qovluğu seç</label>
        <input type="file" name="initial_folder_files" accept=".jpg,.jpeg,.png,.webp,.pdf,.dcm,.dicom" webkitdirectory directory multiple>
      </div>
    </div>

    <div class="actions">
      <button class="btn" type="submit">Yadda saxla</button>
      <a class="btn secondary" href="{{ url_for('index') }}">Ləğv et</a>
    </div>
  </form>
</div>
"""


VIEW_BODY = """
<div class="card">
  <div class="patient-row">
    <div>
      <div class="badge">{{ patient.status }}</div>
      <h1>{{ patient.full_name }}</h1>
      <div class="muted">Telefon: {{ patient.phone or '-' }}</div>
      <div class="muted">Ünvan: {{ patient.address or '-' }}</div>
      <div class="muted">Yaş: {{ patient.age or '-' }} | Cins: {{ patient.gender or '-' }}</div>
      <div class="muted">Müraciət tarixi: {{ patient.admission_date.strftime('%Y-%m-%d') if patient.admission_date else '-' }}</div>
      <div class="muted">Mənbə: {{ patient.referral_source_other if patient.referral_source == 'Digər' and patient.referral_source_other else patient.referral_source or '-' }}</div>
      <div class="muted">Yaradılıb: {{ patient.created_at.strftime('%Y-%m-%d %H:%M') }}</div>
      <div class="muted">Yenilənib: {{ patient.updated_at.strftime('%Y-%m-%d %H:%M') }}</div>
    </div>
    <div class="actions">
      <a class="btn" href="{{ url_for('edit_patient', patient_id=patient.id) }}">Düzəliş et</a>
      <a class="btn secondary" href="{{ url_for('new_session', patient_id=patient.id) }}">Yeni kontrol</a>
      {% if patient.status != 'Arxiv' %}
        <form method="post" action="{{ url_for('archive_patient', patient_id=patient.id) }}">
          <button class="btn warning" type="submit">Arxiv et</button>
        </form>
      {% endif %}
    </div>
  </div>
</div>

<div class="card">
  <h3>Şikayətlər və müddəti</h3>
  <p>{{ patient.complaint or '-' }}</p>
  <p class="muted">Müddət: {{ patient.complaint_duration or '-' }}</p>
</div>

<div class="card">
  <h3>Diaqnoz</h3>
  <p>{{ patient.diagnosis or '-' }}</p>
</div>

<div class="card">
  <h3>Əməliyyat və müalicə keçmişi</h3>
  <p>{{ patient.operation_treatment_history or '-' }}</p>
</div>

<div class="card">
  <h3>Keçirilmiş xəstəliklər, travmalar</h3>
  <p>{{ patient.past_diseases_traumas or '-' }}</p>
</div>

<div class="card">
  <h3>Müalicə planı</h3>
  <p>{{ patient.treatment_plan or '-' }}</p>
</div>

<div class="card">
  <h3>Ümumi qeydlər</h3>
  <p>{{ patient.notes or '-' }}</p>
</div>

<div class="card">
  <h2>Görüntülər / Fayllar</h2>
  <p class="muted">Şəkil, PDF və DICOM faylları Cloudinary üzərindən saxlanılır. Çoxlu .dcm və qovluq yükləmək mümkündür.</p>
  <form method="post" action="{{ url_for('upload_patient_file', patient_id=patient.id) }}" enctype="multipart/form-data">
    <div class="grid-3">
      <div class="field">
        <label>Kateqoriya</label>
        <select name="category" required>
          {% for item in file_categories %}
            <option value="{{ item }}">{{ item }}</option>
          {% endfor %}
        </select>
      </div>
      <div class="field">
        <label>Başlıq</label>
        <input type="text" name="title" placeholder="Məsələn: Servikal MRT, Lomber rentgen">
      </div>
      <div class="field">
        <label>Fayl seç</label>
        <input type="file" name="files" accept=".jpg,.jpeg,.png,.webp,.pdf,.dcm,.dicom" multiple>
      </div>
    </div>
    <div class="field">
      <label>MRT qovluğu seç</label>
      <input type="file" name="folder_files" accept=".jpg,.jpeg,.png,.webp,.pdf,.dcm,.dicom" webkitdirectory directory multiple>
      </div>
    </div>
    <button class="btn" type="submit">Faylı yüklə</button>
  </form>

  <div class="top-space">
    {% if patient.files %}
      {% for f in patient.files %}
        <div class="session-item">
          <div class="badge">{{ f.category }}</div>
          <h3>{{ f.title or f.original_filename }}</h3>
          <div class="muted">Fayl: {{ f.original_filename }}</div>
          <div class="muted">Tarix: {{ f.created_at.strftime('%Y-%m-%d %H:%M') }}</div>
          <div class="actions top-space">
            {% if f.file_ext in ['jpg', 'jpeg', 'png', 'webp', 'pdf'] %}
              <a class="btn secondary" target="_blank" href="{{ get_patient_file_url(f) }}">Aç</a>
            {% endif %}
            <a class="btn" target="_blank" href="{{ get_patient_file_url(f) }}">Yüklə</a>
            <form method="post" action="{{ url_for('delete_patient_file', file_id=f.id) }}">
              <button class="btn danger" type="submit">Sil</button>
            </form>
          </div>
          {% if f.file_ext in ['jpg', 'jpeg', 'png', 'webp'] %}
            <div class="top-space">
              <img src="{{ get_patient_file_url(f) }}" alt="{{ f.original_filename }}" style="max-width:100%; border-radius:12px; border:1px solid var(--line);">
            </div>
          {% elif f.file_ext == 'pdf' %}
            <div class="top-space">
              <iframe src="{{ get_patient_file_url(f) }}" style="width:100%; min-height:500px; border:1px solid var(--line); border-radius:12px;"></iframe>
            </div>
          {% else %}
            <p class="muted top-space">Bu fayl DICOM formatındadır. Sistem onu saxlayır, yükləyə bilir, amma daxildə önizləmə göstərmir.</p>
          {% endif %}
        </div>
      {% endfor %}
    {% else %}
      <div class="empty">Hələ fayl yüklənməyib.</div>
    {% endif %}
  </div>
</div>

<div class="card">
  <div class="patient-row">
    <div>
      <h2>Seans / kontrol tarixçəsi</h2>
      <p class="muted">Hər kontrol ayrıca tarixlə saxlanılır.</p>
    </div>
    <div class="actions">
      <a class="btn" href="{{ url_for('new_session', patient_id=patient.id) }}">Kontrol əlavə et</a>
    </div>
  </div>

  {% if patient.sessions %}
    {% for item in patient.sessions %}
      <div class="session-item">
        <div class="badge">{{ item.status or 'Status yoxdur' }}</div>
        <h3>{{ item.session_date.strftime('%Y-%m-%d') }}</h3>
        <div class="muted">Tip: {{ item.session_type or '-' }}</div>
        <p><strong>Qeyd:</strong><br>{{ item.notes or '-' }}</p>
      </div>
    {% endfor %}
  {% else %}
    <div class="empty">Hələ kontrol qeydi yoxdur.</div>
  {% endif %}
</div>
"""


SESSION_FORM_BODY = """
<div class="card">
  <h1>Yeni kontrol / seans</h1>
  <p class="muted"><strong>Xəstə:</strong> {{ patient.full_name }}</p>
  <form method="post">
    <div class="grid-3">
      <div class="field">
        <label>Tarix</label>
        <input type="date" name="session_date" value="{{ today }}" required>
      </div>
      <div class="field">
        <label>Kontrol tipi</label>
        <input type="text" name="session_type" placeholder="Məsələn: 2-ci kontrol, 5-ci seans">
      </div>
      <div class="field">
        <label>Status</label>
        <select name="status">
          {% for item in statuses %}
            <option value="{{ item }}" {% if item == patient.status %}selected{% endif %}>{{ item }}</option>
          {% endfor %}
        </select>
      </div>
    </div>

    <div class="field">
      <label>Kontrol qeydi</label>
      <textarea name="notes" required placeholder="Məsələn: Ağrı azalıb, hərəkət açıqlığı artıb, ev hərəkətləri davam etdirilsin."></textarea>
    </div>

    <div class="actions">
      <button class="btn" type="submit">Qeydi əlavə et</button>
      <a class="btn secondary" href="{{ url_for('view_patient', patient_id=patient.id) }}">Geri qayıt</a>
    </div>
  </form>
</div>
"""


def render_page(title: str, body_template: str, **context):
    body = render_template_string(body_template, **context)
    return render_template_string(BASE_HTML, title=title, body=body, get_patient_file_url=get_patient_file_url)


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


@app.route('/')
@login_required
def index():
    q = request.args.get('q', '').strip()
    selected_status = request.args.get('status', '').strip()
    selected_source = request.args.get('source', '').strip()
    selected_admission_date = request.args.get('admission_date', '').strip()
    selected_admission_month = request.args.get('admission_month', '').strip()

    query = Patient.query.filter_by(is_deleted=False)

    if q:
        like = f'%{q}%'
        query = query.filter(
            db.or_(
                Patient.full_name.ilike(like),
                Patient.phone.ilike(like),
                Patient.complaint.ilike(like),
                Patient.diagnosis.ilike(like),
                Patient.notes.ilike(like),
                Patient.referral_source.ilike(like),
            )
        )
    if selected_status:
        query = query.filter_by(status=selected_status)
    if selected_source:
        query = query.filter_by(referral_source=selected_source)
    if selected_admission_date:
        exact_date = parse_date(selected_admission_date)
        if exact_date:
            query = query.filter(Patient.admission_date == exact_date)
    elif selected_admission_month:
        try:
            month_start = datetime.strptime(selected_admission_month + '-01', '%Y-%m-%d').date()
            if month_start.month == 12:
                month_end = month_start.replace(year=month_start.year + 1, month=1)
            else:
                month_end = month_start.replace(month=month_start.month + 1)
            query = query.filter(Patient.admission_date >= month_start, Patient.admission_date < month_end)
        except ValueError:
            pass

    patients = query.order_by(Patient.updated_at.desc()).all()
    return render_page(
        'Xəstə bazası',
        INDEX_BODY,
        patients=patients,
        q=q,
        statuses=STATUS_VALUES,
        selected_status=selected_status,
        sources=SOURCE_VALUES,
        selected_source=selected_source,
        selected_admission_date=selected_admission_date,
        selected_admission_month=selected_admission_month,
    )


@app.route('/patients/new', methods=['GET', 'POST'])
@login_required
def new_patient():
    if request.method == 'POST':
        age_value = request.form.get('age', '').strip()
        patient = Patient(
            full_name=request.form['full_name'].strip(),
            age=int(age_value) if age_value else None,
            gender=request.form.get('gender', '').strip(),
            phone=request.form.get('phone', '').strip(),
            address=request.form.get('address', '').strip(),
            referral_source=request.form.get('referral_source', '').strip(),
            referral_source_other=request.form.get('referral_source_other', '').strip(),
            admission_date=parse_date(request.form.get('admission_date', '')),
            complaint=request.form.get('complaint', '').strip(),
            complaint_duration=request.form.get('complaint_duration', '').strip(),
            diagnosis=request.form.get('diagnosis', '').strip(),
            operation_treatment_history=request.form.get('operation_treatment_history', '').strip(),
            past_diseases_traumas=request.form.get('past_diseases_traumas', '').strip(),
            treatment_plan=request.form.get('treatment_plan', '').strip(),
            notes=request.form.get('notes', '').strip(),
            status=request.form.get('status', 'Aktiv').strip() or 'Aktiv',
        )
        db.session.add(patient)
        db.session.commit()

        uploaded_files = normalize_uploaded_files('initial_files', 'initial_folder_files')
        category = request.form.get('initial_file_category', '').strip()
        title = request.form.get('initial_file_title', '').strip()
        saved_count = save_uploaded_files(patient.id, uploaded_files, category, title)
        if saved_count:
            db.session.commit()
            flash(f'Xəstə və {saved_count} fayl uğurla əlavə olundu.')
        else:
            flash('Xəstə uğurla əlavə olundu.')
        return redirect(url_for('view_patient', patient_id=patient.id))

    return render_page(
        'Yeni xəstə',
        PATIENT_FORM_BODY,
        heading='Yeni xəstə əlavə et',
        patient=None,
        statuses=STATUS_VALUES,
        sources=SOURCE_VALUES,
        file_categories=FILE_CATEGORY_VALUES,
    )


@app.route('/patients/<int:patient_id>')
@login_required
def view_patient(patient_id):
    patient = Patient.query.filter_by(id=patient_id, is_deleted=False).first_or_404()
    return render_page(patient.full_name, VIEW_BODY, patient=patient, file_categories=FILE_CATEGORY_VALUES)


@app.route('/patients/<int:patient_id>/edit', methods=['GET', 'POST'])
@login_required
def edit_patient(patient_id):
    patient = Patient.query.filter_by(id=patient_id, is_deleted=False).first_or_404()
    if request.method == 'POST':
        age_value = request.form.get('age', '').strip()
        patient.full_name = request.form['full_name'].strip()
        patient.age = int(age_value) if age_value else None
        patient.gender = request.form.get('gender', '').strip()
        patient.phone = request.form.get('phone', '').strip()
        patient.address = request.form.get('address', '').strip()
        patient.referral_source = request.form.get('referral_source', '').strip()
        patient.referral_source_other = request.form.get('referral_source_other', '').strip()
        patient.admission_date = parse_date(request.form.get('admission_date', ''))
        patient.complaint = request.form.get('complaint', '').strip()
        patient.complaint_duration = request.form.get('complaint_duration', '').strip()
        patient.diagnosis = request.form.get('diagnosis', '').strip()
        patient.operation_treatment_history = request.form.get('operation_treatment_history', '').strip()
        patient.past_diseases_traumas = request.form.get('past_diseases_traumas', '').strip()
        patient.treatment_plan = request.form.get('treatment_plan', '').strip()
        patient.notes = request.form.get('notes', '').strip()
        patient.status = request.form.get('status', 'Aktiv').strip() or 'Aktiv'
        db.session.commit()
        flash('Xəstə məlumatı yeniləndi.')
        return redirect(url_for('view_patient', patient_id=patient.id))

    return render_page(
        'Xəstə redaktə',
        PATIENT_FORM_BODY,
        heading='Xəstə məlumatını düzəlt',
        patient=patient,
        statuses=STATUS_VALUES,
        sources=SOURCE_VALUES,
        file_categories=FILE_CATEGORY_VALUES,
    )


@app.route('/patients/<int:patient_id>/sessions/new', methods=['GET', 'POST'])
@login_required
def new_session(patient_id):
    patient = Patient.query.filter_by(id=patient_id, is_deleted=False).first_or_404()
    if request.method == 'POST':
        session_note = SessionNote(
            patient_id=patient.id,
            session_date=parse_date(request.form.get('session_date', '')) or datetime.utcnow().date(),
            session_type=request.form.get('session_type', '').strip(),
            status=request.form.get('status', '').strip(),
            notes=request.form.get('notes', '').strip(),
        )
        patient.status = session_note.status or patient.status
        db.session.add(session_note)
        db.session.commit()
        flash('Kontrol qeydi əlavə olundu.')
        return redirect(url_for('view_patient', patient_id=patient.id))

    return render_page(
        'Yeni kontrol',
        SESSION_FORM_BODY,
        patient=patient,
        today=datetime.utcnow().date().isoformat(),
        statuses=STATUS_VALUES,
    )




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

    saved_count = 0

    for uploaded in uploaded_files:
        result = save_patient_file_record(
            patient_id=patient.id,
            uploaded_file=uploaded,
            category=category or 'Digər sənəd',
            title=title,
        )
        if result:
            saved_count += 1

    if saved_count == 0:
        flash('Uyğun fayl tapılmadı və ya yükləmə alınmadı.')
    else:
        flash(f'{saved_count} fayl uğurla yükləndi.')

    return redirect(url_for('view_patient', patient_id=patient.id))

@app.route('/uploads/<path:filename>')
@login_required
def serve_patient_file(filename):
    patient_file = PatientFile.query.filter_by(stored_filename=filename).first()
    if patient_file:
        remote_url = get_remote_file_url(patient_file)
        if remote_url:
            return redirect(remote_url)
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)


@app.route('/patient-files/<int:file_id>/delete', methods=['POST'])
@login_required
def delete_patient_file(file_id):
    patient_file = PatientFile.query.get_or_404(file_id)
    patient_id = patient_file.patient_id
    delete_uploaded_file(patient_file)
    db.session.delete(patient_file)
    db.session.commit()
    flash('Fayl silindi.')
    return redirect(url_for('view_patient', patient_id=patient_id))


@app.route('/patients/<int:patient_id>/archive', methods=['POST'])
@login_required
def archive_patient(patient_id):
    patient = Patient.query.filter_by(id=patient_id, is_deleted=False).first_or_404()
    patient.status = 'Arxiv'
    db.session.commit()
    flash('Xəstə arxivə göndərildi.')
    return redirect(url_for('view_patient', patient_id=patient.id))


@app.route('/patients/<int:patient_id>/soft-delete', methods=['POST'])
@login_required
def soft_delete_patient(patient_id):
    patient = Patient.query.filter_by(id=patient_id, is_deleted=False).first_or_404()
    patient.is_deleted = True
    db.session.commit()
    flash('Xəstə siyahıdan gizlədildi.')
    return redirect(url_for('index'))


@app.route('/backup')
@login_required
def backup_info():
    return redirect(url_for('index'))


if __name__ == '__main__':
    app.run(debug=True)
