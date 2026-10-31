python
from flask import Flask, render_template, request, redirect, url_for, session, flash
import mysql.connector
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import cloudinary
import cloudinary.uploader
import cloudinary.utils
import os
import uuid
import logging
import time

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")

app.config.update(
    SESSION_PERMANENT=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
    SESSION_REFRESH_EACH_REQUEST=False,
    MAX_CONTENT_LENGTH=5 * 1024 * 1024
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

UPLOAD_FOLDER = os.path.join(app.root_path, "static", "resumes")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Cloudinary Configuration
cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)


# Database Connection
def get_db():
    try:
        connection = mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            port=int(os.getenv("DB_PORT", "22718")),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME"),
            connection_timeout=8,
            read_timeout=10,
            write_timeout=10,
            ssl_disabled=False,
            autocommit=False
        )

        logger.info("Aiven MySQL connected successfully")
        return connection

    except mysql.connector.Error:
        logger.exception("Aiven MySQL connection failed")
        raise


# Role Checks
def is_recruiter():
    return "user_id" in session and session.get("user_role") == "recruiter"


def is_candidate():
    return "user_id" in session and session.get("user_role") == "candidate"


def candidate_required():
    if "user_id" not in session:
        flash("Please login first.")
        return redirect(url_for("login"))

    if not is_candidate():
        flash("Only candidates can access this page.")
        return redirect(url_for("recruiter_dashboard"))

    return None


# Cloudinary Resume Upload
def upload_resume(resume, user_id):
    if not resume or not resume.filename:
        return None

    if not resume.filename.lower().endswith(".pdf"):
        raise ValueError("Only PDF resume files are allowed.")

    safe_name = secure_filename(resume.filename)

    if not safe_name:
        raise ValueError("Invalid resume filename.")

    if resume.mimetype not in (
        "application/pdf",
        "application/octet-stream"
    ):
        raise ValueError("Please upload a valid PDF file.")

    public_id = f"job_portal/resumes/{user_id}_{uuid.uuid4().hex}"

    result = cloudinary.uploader.upload(
        resume,
        resource_type="raw",
        type="authenticated",
        public_id=public_id,
        overwrite=False
    )

    return result["public_id"]


# Protected Resume URL
def get_resume_url(public_id):
    if not public_id:
        return None

    return cloudinary.utils.private_download_url(
        public_id,
        "pdf",
        resource_type="raw",
        type="authenticated",
        expires_at=int(time.time()) + 3600
    )


# Get Jobs
def get_jobs(keyword="", location="", job_type=""):
    connection = get_db()
    cursor = connection.cursor(dictionary=True)

    try:
        query = """
            SELECT jobs.id, jobs.title, jobs.location, jobs.salary,
                   jobs.job_type, jobs.job_type AS type,
                   jobs.description, companies.company_name,
                   companies.company_name AS company
            FROM jobs
            LEFT JOIN companies ON jobs.company_id = companies.id
            WHERE 1=1
        """

        values = []

        if keyword:
            query += " AND jobs.title LIKE %s"
            values.append("%" + keyword + "%")

        if location:
            query += " AND jobs.location LIKE %s"
            values.append("%" + location + "%")

        if job_type:
            query += " AND jobs.job_type = %s"
            values.append(job_type)

        query += " ORDER BY jobs.id DESC"

        cursor.execute(query, tuple(values))
        return cursor.fetchall()

    finally:
        cursor.close()
        connection.close()


# Home
@app.route("/")
def home():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if is_recruiter():
        return redirect(url_for("recruiter_dashboard"))

    return render_template(
        "index.html",
        jobs=get_jobs(),
        user_name=session.get("user_name")
    )


# Register
@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("home"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        role = request.form.get("role", "candidate")

        if not name or not email or not password:
            flash("Please fill all required fields.")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must contain at least 8 characters.")
            return redirect(url_for("register"))

        if role not in ["candidate", "recruiter"]:
            flash("Invalid account role.")
            return redirect(url_for("register"))

        connection = None
        cursor = None

        try:
            connection = get_db()
            cursor = connection.cursor()

            cursor.execute(
                """
                INSERT INTO users (name, email, password, role)
                VALUES (%s, %s, %s, %s)
                """,
                (
                    name,
                    email,
                    generate_password_hash(password),
                    role
                )
            )

            connection.commit()

            flash("Registration successful! Please login.")
            return redirect(url_for("login"))

        except mysql.connector.IntegrityError:
            if connection:
                connection.rollback()

            flash("This email is already registered.")
            return redirect(url_for("register"))

        except Exception:
            if connection:
                connection.rollback()

            logger.exception("Registration failed")
            flash("Registration failed. Please try again later.")
            return redirect(url_for("register"))

        finally:
            if cursor:
                cursor.close()

            if connection and connection.is_connected():
                connection.close()

    return render_template("register.html")


# Login
@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        return redirect(url_for("home"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        connection = None
        cursor = None

        try:
            connection = get_db()
            cursor = connection.cursor(dictionary=True)

            cursor.execute(
                "SELECT * FROM users WHERE email = %s",
                (email,)
            )

            user = cursor.fetchone()

            if user and check_password_hash(user["password"], password):
                session.clear()
                session.permanent = False
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                session["user_role"] = user["role"]

                flash("Login successful!")

                if user["role"] == "recruiter":
                    return redirect(url_for("recruiter_dashboard"))

                return redirect(url_for("home"))

            flash("Invalid email or password.")

        except Exception:
            logger.exception("Login failed")
            flash("Login is temporarily unavailable. Please try again.")

        finally:
            if cursor:
                cursor.close()

            if connection and connection.is_connected():
                connection.close()

    return render_template("login.html")


# Candidate Profile
@app.route("/profile", methods=["GET", "POST"])
def candidate_profile():
    response = candidate_required()

    if response:
        return response

    connection = None
    cursor = None

    try:
        connection = get_db()
        cursor = connection.cursor(dictionary=True)

        if request.method == "POST":
            phone = request.form.get("phone", "").strip()
            location = request.form.get("location", "").strip()
            qualification = request.form.get("qualification", "").strip()
            college = request.form.get("college", "").strip()
            passing_year = request.form.get("passing_year", "").strip()
            skills = request.form.get("skills", "").strip()
            experience = request.form.get("experience", "").strip()

            if not all([
                phone, location, qualification, college,
                passing_year, skills, experience
            ]):
                flash("Please fill all profile details.")
                return redirect(url_for("candidate_profile"))

            cursor.execute(
                """
                SELECT resume_filename, resume_public_id
                FROM candidate_profiles
                WHERE user_id = %s
                """,
                (session["user_id"],)
            )

            old_profile = cursor.fetchone()

            resume_filename = (
                old_profile["resume_filename"] if old_profile else None
            )

            resume_public_id = (
                old_profile["resume_public_id"] if old_profile else None
            )

            resume = request.files.get("resume")

            if resume and resume.filename:
                resume_public_id = upload_resume(
                    resume,
                    session["user_id"]
                )
                resume_filename = None

            cursor.execute(
                """
                INSERT INTO candidate_profiles
                (user_id, phone, location, qualification, college,
                 passing_year, skills, experience, resume_filename,
                 resume_public_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                phone = VALUES(phone),
                location = VALUES(location),
                qualification = VALUES(qualification),
                college = VALUES(college),
                passing_year = VALUES(passing_year),
                skills = VALUES(skills),
                experience = VALUES(experience),
                resume_filename = VALUES(resume_filename),
                resume_public_id = VALUES(resume_public_id)
                """,
                (
                    session["user_id"], phone, location,
                    qualification, college, passing_year,
                    skills, experience, resume_filename,
                    resume_public_id
                )
            )

            connection.commit()

            flash("Profile saved successfully!")
            return redirect(url_for("candidate_profile"))

        cursor.execute(
            """
            SELECT * FROM candidate_profiles
            WHERE user_id = %s
            """,
            (session["user_id"],)
        )

        profile = cursor.fetchone()

        return render_template("profile.html", profile=profile)

    except ValueError as error:
        if connection:
            connection.rollback()

        flash(str(error))
        return redirect(url_for("candidate_profile"))

    except Exception:
        if connection:
            connection.rollback()

        logger.exception("Profile error")
        flash("Could not save profile. Please try again.")

    finally:
        if cursor:
            cursor.close()

        if connection and connection.is_connected():
            connection.close()

    return redirect(url_for("candidate_profile"))


# Candidate Resume View
@app.route("/my-resume")
def my_resume():
    if not is_candidate():
        flash("Please login as a candidate.")
        return redirect(url_for("login"))

    connection = get_db()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT resume_public_id, resume_filename
            FROM candidate_profiles
            WHERE user_id = %s
            """,
            (session["user_id"],)
        )

        profile = cursor.fetchone()

        if not profile:
            return "Resume not found.", 404

        if profile.get("resume_public_id"):
            return redirect(
                get_resume_url(profile["resume_public_id"])
            )

        if profile.get("resume_filename"):
            return redirect(
                url_for(
                    "static",
                    filename="resumes/" + profile["resume_filename"]
                )
            )

        return "Resume not found.", 404

    finally:
        cursor.close()
        connection.close()


# Logout
@app.route("/logout")
def logout():
    session.clear()

    response = redirect(url_for("login"))

    response.delete_cookie(
        app.config.get("SESSION_COOKIE_NAME", "session")
    )

    flash("You have been logged out successfully.")

    return response


# Recruiter Dashboard
@app.route("/recruiter/dashboard")
def recruiter_dashboard():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if not is_recruiter():
        flash("Only recruiters can access this page.")
        return redirect(url_for("home"))

    connection = get_db()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT jobs.*, companies.company_name
            FROM jobs
            LEFT JOIN companies ON jobs.company_id = companies.id
            WHERE jobs.recruiter_id = %s
            ORDER BY jobs.id DESC
            """,
            (session["user_id"],)
        )

        recruiter_jobs = cursor.fetchall()

    finally:
        cursor.close()
        connection.close()

    return render_template(
        "recruiter_dashboard.html",
        jobs=recruiter_jobs
    )


# Post Job
@app.route("/recruiter/post-job", methods=["GET", "POST"])
def post_job():
    if "user_id" not in session:
        return redirect(url_for("login"))

    if not is_recruiter():
        flash("Only recruiters can post jobs.")
        return redirect(url_for("home"))

    if request.method == "POST":
        title = request.form.get("title", "").strip()
        company_name = request.form.get("company_name", "").strip()
        location = request.form.get("location", "").strip()
        salary = request.form.get("salary", "").strip()
        job_type = request.form.get("job_type", "").strip()
        description = request.form.get("description", "").strip()

        if not all([
            title, company_name, location,
            salary, job_type, description
        ]):
            flash("Please fill all fields.")
            return redirect(url_for("post_job"))

        if job_type not in ["Full Time", "Part Time", "Internship"]:
            flash("Please select a valid job type.")
            return redirect(url_for("post_job"))

        connection = get_db()
        cursor = connection.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO companies (company_name, location)
                VALUES (%s, %s)
                """,
                (company_name, location)
            )

            company_id = cursor.lastrowid

            cursor.execute(
                """
                INSERT INTO jobs
                (title, company_id, location, salary, job_type,
                 description, recruiter_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    title, company_id, location, salary,
                    job_type, description, session["user_id"]
                )
            )

            connection.commit()

            flash("Job posted successfully!")
            return redirect(url_for("recruiter_dashboard"))

        except Exception:
            connection.rollback()

            logger.exception("Job posting failed")
            flash("Something went wrong while posting the job.")

            return redirect(url_for("post_job"))

        finally:
            cursor.close()
            connection.close()

    return render_template("post_job.html")


# Browse Jobs
@app.route("/jobs")
def search_jobs():
    response = candidate_required()

    if response:
        return response

    keyword = request.args.get("keyword", "").strip()
    location = request.args.get("location", "").strip()
    job_type = request.args.get("job_type", "").strip()

    return render_template(
        "jobs.html",
        jobs=get_jobs(keyword, location, job_type),
        keyword=keyword,
        location=location,
        job_type=job_type
    )


# Job Details
@app.route("/job/<int:job_id>")
def job_detail(job_id):
    response = candidate_required()

    if response:
        return response

    connection = get_db()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT jobs.*, companies.company_name,
                   companies.company_name AS company
            FROM jobs
            LEFT JOIN companies ON jobs.company_id = companies.id
            WHERE jobs.id = %s
            """,
            (job_id,)
        )

        job = cursor.fetchone()

    finally:
        cursor.close()
        connection.close()

    if not job:
        return "Job not found!", 404

    return render_template("job_detail.html", job=job)


# Apply Form
@app.route("/apply/<int:job_id>", methods=["GET", "POST"])
def apply_job(job_id):
    response = candidate_required()

    if response:
        return response

    connection = get_db()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT jobs.id, jobs.title, jobs.location, jobs.salary,
                   jobs.job_type, companies.company_name
            FROM jobs
            LEFT JOIN companies ON jobs.company_id = companies.id
            WHERE jobs.id = %s
            """,
            (job_id,)
        )

        job = cursor.fetchone()

        if not job:
            flash("Job not found.")
            return redirect(url_for("search_jobs"))

        cursor.execute(
            """
            SELECT * FROM candidate_profiles
            WHERE user_id = %s
            """,
            (session["user_id"],)
        )

        profile = cursor.fetchone()

        if request.method == "GET":
            cursor.execute(
                """
                SELECT id FROM applications
                WHERE user_id = %s AND job_id = %s
                """,
                (session["user_id"], job_id)
            )

            if cursor.fetchone():
                flash("You have already applied for this job.")
                return redirect(url_for("my_applications"))

            cursor.execute(
                "SELECT email FROM users WHERE id = %s",
                (session["user_id"],)
            )

            user = cursor.fetchone()

            return render_template(
                "apply.html",
                job=job,
                profile=profile,
                user_name=session.get("user_name", ""),
                user_email=user["email"] if user else ""
            )

        phone = request.form.get("phone", "").strip()
        location = request.form.get("location", "").strip()
        qualification = request.form.get("qualification", "").strip()
        college = request.form.get("college", "").strip()
        passing_year = request.form.get("passing_year", "").strip()
        skills = request.form.get("skills", "").strip()
        experience = request.form.get("experience", "").strip()

        if not all([
            phone, location, qualification, college,
            passing_year, skills, experience
        ]):
            flash("Please complete all required details.")
            return redirect(url_for("apply_job", job_id=job_id))

        resume_filename = (
            profile["resume_filename"] if profile else None
        )

        resume_public_id = (
            profile["resume_public_id"] if profile else None
        )

        resume = request.files.get("resume")

        if resume and resume.filename:
            resume_public_id = upload_resume(
                resume,
                session["user_id"]
            )
            resume_filename = None

        if not resume_public_id and not resume_filename:
            flash("Please upload your PDF resume.")
            return redirect(url_for("apply_job", job_id=job_id))

        cursor.execute(
            """
            INSERT INTO candidate_profiles
            (user_id, phone, location, qualification, college,
             passing_year, skills, experience, resume_filename,
             resume_public_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
            phone = VALUES(phone),
            location = VALUES(location),
            qualification = VALUES(qualification),
            college = VALUES(college),
            passing_year = VALUES(passing_year),
            skills = VALUES(skills),
            experience = VALUES(experience),
            resume_filename = VALUES(resume_filename),
            resume_public_id = VALUES(resume_public_id)
            """,
            (
                session["user_id"], phone, location,
                qualification, college, passing_year,
                skills, experience, resume_filename,
                resume_public_id
            )
        )

        cursor.execute(
            """
            SELECT id FROM applications
            WHERE user_id = %s AND job_id = %s
            """,
            (session["user_id"], job_id)
        )

        if cursor.fetchone():
            connection.rollback()
            flash("You have already applied for this job.")
            return redirect(url_for("my_applications"))

        cursor.execute(
            """
            INSERT INTO applications (user_id, job_id, status)
            VALUES (%s, %s, %s)
            """,
            (session["user_id"], job_id, "Pending")
        )

        connection.commit()

        flash("Your application has been submitted successfully!")
        return redirect(url_for("my_applications"))

    except ValueError as error:
        connection.rollback()
        flash(str(error))
        return redirect(url_for("apply_job", job_id=job_id))

    except Exception:
        connection.rollback()

        logger.exception("Application failed")
        flash("Could not submit your application. Please try again.")

        return redirect(url_for("job_detail", job_id=job_id))

    finally:
        cursor.close()
        connection.close()


# My Applications
@app.route("/my-applications")
def my_applications():
    response = candidate_required()

    if response:
        return response

    connection = get_db()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT applications.id, applications.status,
                   jobs.id AS job_id, jobs.title, jobs.location,
                   jobs.salary, jobs.job_type, companies.company_name
            FROM applications
            JOIN jobs ON applications.job_id = jobs.id
            LEFT JOIN companies ON jobs.company_id = companies.id
            WHERE applications.user_id = %s
            ORDER BY applications.id DESC
            """,
            (session["user_id"],)
        )

        application_list = cursor.fetchall()

    finally:
        cursor.close()
        connection.close()

    return render_template(
        "my_applications.html",
        applications=application_list
    )


# Recruiter - View Applicants
@app.route("/recruiter/job/<int:job_id>/applicants")
def view_applicants(job_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if not is_recruiter():
        flash("Only recruiters can access this page.")
        return redirect(url_for("home"))

    connection = get_db()
    cursor = connection.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT id, title FROM jobs
            WHERE id = %s AND recruiter_id = %s
            """,
            (job_id, session["user_id"])
        )

        owned_job = cursor.fetchone()

        if not owned_job:
            return "Job not found or access denied.", 404

        cursor.execute(
            """
            SELECT applications.id, applications.status,
                   users.name, users.email,
                   candidate_profiles.phone,
                   candidate_profiles.location,
                   candidate_profiles.qualification,
                   candidate_profiles.college,
                   candidate_profiles.passing_year,
                   candidate_profiles.skills,
                   candidate_profiles.experience,
                   candidate_profiles.resume_filename,
                   candidate_profiles.resume_public_id
            FROM applications
            JOIN users ON applications.user_id = users.id
            LEFT JOIN candidate_profiles
                ON applications.user_id = candidate_profiles.user_id
            WHERE applications.job_id = %s
            ORDER BY applications.id DESC
            """,
            (job_id,)
        )

        applicants = cursor.fetchall()

        for applicant in applicants:
            applicant["resume_url"] = None

            if applicant.get("resume_public_id"):
                applicant["resume_url"] = get_resume_url(
                    applicant["resume_public_id"]
                )

            elif applicant.get("resume_filename"):
                applicant["resume_url"] = url_for(
                    "static",
                    filename="resumes/" + applicant["resume_filename"]
                )

    finally:
        cursor.close()
        connection.close()

    return render_template(
        "applicants.html",
        applicants=applicants,
        job_id=job_id,
        job=owned_job
    )


# Recruiter - Update Application Status
@app.route(
    "/recruiter/application/<int:application_id>/status",
    methods=["POST"]
)
def update_application_status(application_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    if not is_recruiter():
        flash("Only recruiters can update application status.")
        return redirect(url_for("home"))

    new_status = request.form.get("status", "").strip()

    allowed_statuses = [
        "Pending",
        "Shortlisted",
        "Rejected",
        "Selected"
    ]

    if new_status not in allowed_statuses:
        flash("Invalid application status.")
        return redirect(url_for("recruiter_dashboard"))

    connection = get_db()
    cursor = connection.cursor()

    try:
        cursor.execute(
            """
            SELECT applications.job_id
            FROM applications
            JOIN jobs ON applications.job_id = jobs.id
            WHERE applications.id = %s
            AND jobs.recruiter_id = %s
            """,
            (application_id, session["user_id"])
        )

        application = cursor.fetchone()

        if not application:
            flash("Application not found or access denied.")
            return redirect(url_for("recruiter_dashboard"))

        job_id = application[0]

        cursor.execute(
            """
            UPDATE applications
            SET status = %s
            WHERE id = %s
            """,
            (new_status, application_id)
        )

        connection.commit()

        flash("Application status updated successfully!")

        return redirect(
            url_for("view_applicants", job_id=job_id)
        )

    except Exception:
        connection.rollback()

        logger.exception("Status update failed")
        flash("Could not update application status.")

        return redirect(url_for("recruiter_dashboard"))

    finally:
        cursor.close()
        connection.close()


if __name__ == "__main__":
    app.run(debug=True)
```