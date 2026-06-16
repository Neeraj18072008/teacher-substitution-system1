from flask import Flask, render_template, request, redirect, session, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
import pandas as pd
import os
from datetime import datetime, date
from functools import wraps

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")

# DATABASE CONFIG
basedir = os.path.abspath(os.path.dirname(__file__))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///' + os.path.join(basedir, 'instance', 'database.db').replace('\\', '/')
app.config['UPLOAD_FOLDER'] = 'upload'

db = SQLAlchemy(app)

# =========================
# DATABASE MODELS
# =========================

class School(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), unique=True, nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    teachers = db.relationship('Teacher', backref='school', lazy=True, cascade='all, delete-orphan')
    timetables = db.relationship('Timetable', backref='school', lazy=True, cascade='all, delete-orphan')
    absences = db.relationship('Absence', backref='school', lazy=True, cascade='all, delete-orphan')
    substitutions = db.relationship('Substitution', backref='school', lazy=True, cascade='all, delete-orphan')
    
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
    
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

class Teacher(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'), nullable=False)
    name = db.Column(db.String(100), nullable=False)
    subject = db.Column(db.String(100), nullable=False)
    wing = db.Column(db.String(50))

    timetables = db.relationship('Timetable', backref='teacher', lazy=True, cascade='all, delete-orphan')
    absences = db.relationship('Absence', backref='teacher', lazy=True, cascade='all, delete-orphan')
    substitutions = db.relationship('Substitution', backref='substitute_teacher', lazy=True, foreign_keys='Substitution.substitute_teacher_id')

class Timetable(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=False)
    period = db.Column(db.Integer, nullable=False)
    day = db.Column(db.String(20), nullable=False)  # Monday, Tuesday, etc
    class_name = db.Column(db.String(50), nullable=False)
    version = db.Column(db.Integer, default=1)  # Track timetable versions
    created_at = db.Column(db.DateTime, default=datetime.now)

class Absence(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'), nullable=False)
    teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=False)
    absent_date = db.Column(db.Date, nullable=False)
    reason = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.now)
    
    substitutions = db.relationship('Substitution', backref='absent_teacher_info', lazy=True, cascade='all, delete-orphan')

class Substitution(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    school_id = db.Column(db.Integer, db.ForeignKey('school.id'), nullable=False)
    absence_id = db.Column(db.Integer, db.ForeignKey('absence.id'), nullable=False)
    substitute_teacher_id = db.Column(db.Integer, db.ForeignKey('teacher.id'), nullable=False)
    period = db.Column(db.Integer, nullable=False)
    class_name = db.Column(db.String(50), nullable=False)
    substitution_date = db.Column(db.Date, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.now)

# CREATE DATABASE
# Ensure instance directory exists
os.makedirs('instance', exist_ok=True)

with app.app_context():
    db.create_all()

# =========================
# HELPER FUNCTIONS
# =========================

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'school_id' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated_function

def get_teacher_substitution_count(teacher_id, target_date):
    """Get count of substitutions for a teacher on a specific date"""
    return Substitution.query.filter_by(
        substitute_teacher_id=teacher_id,
        substitution_date=target_date
    ).count()

def is_teacher_free_in_period(teacher_id, period, target_date):
    """Check if teacher is free in a specific period on a date"""
    # Check if teacher has timetable on this period
    day_name = target_date.strftime('%A')  # Returns "Monday", "Tuesday", etc.
    
    # Get all timetable entries for this teacher, period and check day case-insensitively
    all_classes = Timetable.query.filter_by(
        teacher_id=teacher_id,
        period=period
    ).all()
    
    # Check if any matches the day (case-insensitive)
    has_class = any(t.day.title() == day_name for t in all_classes)
    
    if has_class:
        return False
    
    # Check if already assigned as substitute
    already_assigned = Substitution.query.filter_by(
        substitute_teacher_id=teacher_id,
        period=period,
        substitution_date=target_date
    ).first()
    
    return not already_assigned

def find_best_substitute(school_id, absent_teacher_id, period, target_date, subject, wing):
    """
    Find the best teacher to substitute
    Uses fair distribution logic
    """
    available_teachers = Teacher.query.filter_by(school_id=school_id).all()
    
    candidates = []
    
    for teacher in available_teachers:
        if teacher.id == absent_teacher_id:
            continue
        
        if not is_teacher_free_in_period(teacher.id, period, target_date):
            continue
        
        # Count substitutions today
        sub_count = get_teacher_substitution_count(teacher.id, target_date)
        
        # Subject match (bonus)
        subject_match = teacher.subject.lower() == subject.lower()
        wing_match = teacher.wing == wing
        
        candidates.append({
            'teacher': teacher,
            'sub_count': sub_count,
            'subject_match': subject_match,
            'wing_match': wing_match
        })
    
    if not candidates:
        return None
    
    # Sort by: wing match first, then least substitutions today, then subject match
    candidates.sort(key=lambda x: (
        not x['wing_match'],
        x['sub_count'],
        not x['subject_match']
    ))
    
    return candidates[0]['teacher']

# =========================
# AUTHENTICATION ROUTES
# =========================

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        school_name = request.form.get('school_name')
        email = request.form.get('email')
        password = request.form.get('password')
        
        if School.query.filter_by(email=email).first():
            return render_template('register.html', error='Email already registered')
        
        school = School(name=school_name, email=email)
        school.set_password(password)
        
        db.session.add(school)
        db.session.commit()
        
        return redirect('/login')
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        school = School.query.filter_by(email=email).first()
        
        if school and school.check_password(password):
            session['school_id'] = school.id
            session['school_name'] = school.name
            return redirect('/dashboard')
        
        return render_template('login.html', error='Invalid credentials')
    
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

# =========================
# MAIN ROUTES
# =========================

@app.route('/')
def index():
    if 'school_id' in session:
        return redirect('/dashboard')

    return render_template('index.html')

@app.route('/dashboard')
@login_required
def dashboard():
    school = School.query.get(session['school_id'])
    if not school:
        session.clear()
        return redirect('/login')
    
    teachers_count = len(school.teachers)
    timetables_count = len(school.timetables)
    
    return render_template('dashboard.html', 
                         teachers_count=teachers_count,
                         timetables_count=timetables_count,
                         school=school)

# =========================
# TIMETABLE MANAGEMENT
# =========================

@app.route('/upload-timetable', methods=['POST'])
@login_required
def upload_timetable():
    school_id = session['school_id']
    file = request.files.get('file')
    
    if not file:
        return jsonify({'error': 'No file provided'}), 400
    
    try:
        filepath = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        file.save(filepath)
        
        if file.filename.endswith('.csv'):
            df = pd.read_csv(filepath)
        elif file.filename.endswith('.xlsx'):
            df = pd.read_excel(filepath)
        else:
            return jsonify({'error': 'Only CSV or XLSX files allowed'}), 400
        
        required_columns = [
            'Teacher',
            'Subject',
            'Period',
            'Day',
            'Class',
            'Wing'
        ]
        for col in required_columns:
            if col not in df.columns:
                return jsonify({
                    'error': f'Missing column: {col}'
                }), 400
        
        # Expected columns: Teacher, Subject, Period, Day, Class
        for index, row in df.iterrows():
            # Normalize data
            teacher_name = str(row['Teacher']).strip()  # Remove whitespace
            period_str = str(row['Period']).strip().upper()  # Remove whitespace and uppercase
            
            # Extract period number (handle both "2" and "P2" formats)
            if period_str.startswith('P'):
                period_num = int(period_str[1:])  # "P2" -> 2
            else:
                period_num = int(period_str)  # "2" -> 2
            
            # Normalize day name to title case (Monday, Tuesday, etc.)
            day_name = str(row['Day']).strip().title()
            
            # Create or get teacher
            teacher = Teacher.query.filter_by(
                school_id=school_id,
                name=teacher_name
            ).first()
            
            if not teacher:
                teacher = Teacher(
                    school_id=school_id,
                    name=teacher_name,
                    subject=row['Subject'],
                    wing=str(row['Wing']).strip().upper()
                )
                db.session.add(teacher)
                db.session.flush()
            else:
                teacher.wing = str(row['Wing']).strip().upper()
            
            # Create timetable entry
            timetable = Timetable(
                school_id=school_id,
                teacher_id=teacher.id,
                period=period_num,
                day=day_name,
                class_name=row['Class']
            )
            db.session.add(timetable)
        
        db.session.commit()
        os.remove(filepath)
        
        return jsonify({'success': True, 'message': 'Timetable uploaded successfully'})
    
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400

# =========================
# SUBSTITUTION MANAGEMENT
# =========================

@app.route('/mark-absence', methods=['POST'])
@login_required
def mark_absence():
    school_id = session['school_id']
    data = request.get_json()
    
    try:
        teacher_id = data.get('teacher_id')
        absent_date = datetime.strptime(data.get('date'), '%Y-%m-%d').date()
        reason = data.get('reason', '')
        
        print(f"[DEBUG] Mark absence - School: {school_id}, Teacher: {teacher_id}, Date: {absent_date}, Reason: {reason}")
        
        # Check if already marked
        existing = Absence.query.filter_by(
            school_id=school_id,
            teacher_id=teacher_id,
            absent_date=absent_date
        ).first()
        
        if existing:
            print(f"[DEBUG] Absence already exists")
            return jsonify({'error': 'Already marked absent'}), 400
        
        absence = Absence(
            school_id=school_id,
            teacher_id=teacher_id,
            absent_date=absent_date,
            reason=reason
        )
        db.session.add(absence)
        db.session.commit()
        
        print(f"[DEBUG] Absence created with ID: {absence.id}")
        
        return jsonify({'success': True, 'absence_id': absence.id})
    
    except Exception as e:
        print(f"[ERROR] Mark absence error: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e), 'success': False}), 400

@app.route('/auto-assign-substitutes', methods=['POST'])
@login_required
def auto_assign_substitutes():
    """Automatically assign substitutes for all absent teachers on a date"""
    school_id = session['school_id']
    data = request.get_json()
    
    try:
        target_date = datetime.strptime(data.get('date'), '%Y-%m-%d').date()
        print(f"\n{'='*60}")
        print(f"[DEBUG] AUTO-ASSIGN SUBSTITUTES START")
        print(f"[DEBUG] School ID: {school_id}")
        print(f"[DEBUG] Target Date: {target_date}")
        print(f"[DEBUG] Day Name: {target_date.strftime('%A')}")
        print(f"{'='*60}")
        
        # Check total absences for this school and date
        all_absences = Absence.query.filter_by(absent_date=target_date).all()
        print(f"[DEBUG] Total absences in DB for {target_date}: {len(all_absences)}")
        for abs_record in all_absences:
            print(f"  - Absence ID: {abs_record.id}, School: {abs_record.school_id}, Teacher: {abs_record.teacher_id}")
        
        # Now filter by school
        absences = Absence.query.filter_by(
            school_id=school_id,
            absent_date=target_date
        ).all()
        
        print(f"[DEBUG] Absences for THIS SCHOOL ({school_id}) on {target_date}: {len(absences)}")
        
        if len(absences) == 0:
            print(f"[WARNING] NO ABSENCES FOUND!")
            print(f"[DEBUG] Checking database state...")
            all_teachers = Teacher.query.filter_by(school_id=school_id).all()
            print(f"[DEBUG] School has {len(all_teachers)} teachers")
            for t in all_teachers:
                print(f"  - Teacher: {t.name} (ID: {t.id})")
        
        assignments = []
        
        for absence in absences:
            absent_teacher = Teacher.query.get(absence.teacher_id)
            day_name = target_date.strftime('%A')
            
            print(f"\n[DEBUG] Processing absence for teacher: {absent_teacher.name} (ID: {absence.teacher_id})")
            print(f"[DEBUG] Looking for day: {day_name}")
            
            # Try exact match first
            classes = Timetable.query.filter_by(
                school_id=school_id,
                teacher_id=absence.teacher_id,
                day=day_name
            ).all()
            
            print(f"[DEBUG] Exact match 'Classes on {day_name}': {len(classes)}")
            
            # If no exact match, try case-insensitive
            if len(classes) == 0:
                print(f"[DEBUG] Trying case-insensitive search...")
                # Get all timetable entries for this teacher and check day case-insensitively
                all_day_entries = Timetable.query.filter_by(
                    school_id=school_id,
                    teacher_id=absence.teacher_id
                ).all()
                
                classes = [t for t in all_day_entries if t.day.upper() == day_name.upper()]
                print(f"[DEBUG] Case-insensitive match: {len(classes)}")
                
                # Show what days ARE in the database
                unique_days = set([t.day for t in all_day_entries])
                print(f"[DEBUG] Days in database: {unique_days}")
            
            print(f"[DEBUG] Final classes on {day_name}: {len(classes)}")
            
            if len(classes) == 0:
                print(f"[WARNING] Teacher {absent_teacher.name} has NO classes on {day_name}")
                all_timetables = Timetable.query.filter_by(teacher_id=absence.teacher_id).all()
                print(f"[DEBUG] Teacher has {len(all_timetables)} timetable entries total")
            
            for timetable in classes:
                print(f"  Processing Period {timetable.period}: {timetable.class_name}")
                
                existing_sub = Substitution.query.filter_by(
                    absence_id=absence.id,
                    period=timetable.period,
                    substitution_date=target_date
                ).first()
                
                if existing_sub:
                    print(f"    Already has substitute")
                    continue
                
                substitute = find_best_substitute(
                    school_id,
                    absence.teacher_id,
                    timetable.period,
                    target_date,
                    absent_teacher.subject,
                    absent_teacher.wing
                )
                
                if substitute:
                    print(f"    Found substitute: {substitute.name}")
                    sub_record = Substitution(
                        school_id=school_id,
                        absence_id=absence.id,
                        substitute_teacher_id=substitute.id,
                        period=timetable.period,
                        class_name=timetable.class_name,
                        substitution_date=target_date
                    )
                    db.session.add(sub_record)
                    
                    assignments.append({
                        'absent': absent_teacher.name,
                        'substitute': substitute.name,
                        'period': timetable.period,
                        'class': timetable.class_name
                    })
                else:
                    print(f"    NO SUBSTITUTE AVAILABLE")
        
        db.session.commit()
        print(f"\n[DEBUG] Final assignments: {len(assignments)}")
        print(f"{'='*60}\n")
        
        return jsonify({'success': True, 'assignments': assignments})
    
    except Exception as e:
        print(f"[ERROR] Auto-assign error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e), 'success': False}), 400

@app.route('/substitution-history')
@login_required
def substitution_history():
    school_id = session['school_id']
    subs = Substitution.query.filter_by(school_id=school_id).all()
    
    history = []
    for sub in subs:
        absence = Absence.query.get(sub.absence_id)
        absent_teacher = Teacher.query.get(absence.teacher_id)
        substitute = Teacher.query.get(sub.substitute_teacher_id)
        
        history.append({
            'date': sub.substitution_date,
            'absent_teacher': absent_teacher.name,
            'substitute': substitute.name,
            'period': sub.period,
            'class': sub.class_name
        })
    
    return render_template('substitution_history.html', history=history)

# =========================
# API ENDPOINTS
# =========================

@app.route('/api/teachers')
@login_required
def api_teachers():
    school_id = session['school_id']
    teachers = Teacher.query.filter_by(school_id=school_id).all()
    return jsonify([{
        'id': t.id,
        'name': t.name,
        'subject': t.subject
    } for t in teachers])

@app.route('/api/timetable')
@login_required
def api_timetable():
    school_id = session['school_id']
    timetables = Timetable.query.filter_by(school_id=school_id).all()
    return jsonify([{
        'id': t.id,
        'teacher': t.teacher.name,
        'subject': t.teacher.subject,
        'period': t.period,
        'day': t.day,
        'class': t.class_name
    } for t in timetables])

if __name__ == '__main__':
    app.run()