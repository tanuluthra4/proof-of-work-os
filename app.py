import os
import sqlite3

from flask import Flask, render_template, request, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash

from config import DB_PATH, SECRET_KEY
from database.db import get_db_connection
from routes.projects import projects_bp
from routes.tasks import tasks_bp


app = Flask(__name__)
app.secret_key = SECRET_KEY
app.register_blueprint(projects_bp)
app.register_blueprint(tasks_bp)

def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

    conn = get_db_connection()

    schema_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "database",
        "schema.sql"
    )

    with open(schema_path, "r", encoding="utf-8") as schema_file:
        conn.executescript(schema_file.read())

    conn.close()


@app.route('/')
def home():
    return redirect('/login')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    error = None

    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])

        conn = get_db_connection()

        try:
            cursor = conn.cursor()

            cursor.execute(
                """
                INSERT INTO users (username, email, password)
                VALUES (?, ?, ?)
                """,
                (username, email, password)
            )

            user_id = cursor.lastrowid

            cursor.execute(
                """
                INSERT INTO profiles (user_id, full_name)
                VALUES (?, ?)
                """,
                (user_id, username)
            )

            conn.commit()

            session['user'] = username
            session['email'] = email
            session['user_id'] = user_id

            return redirect('/dashboard')

        except sqlite3.IntegrityError:
            error = "Email already exists"

        finally:
            conn.close()

    return render_template('signup.html', error=error)


@app.route('/login', methods=['GET', 'POST'])
def login():
    error = None

    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = get_db_connection()

        user = conn.execute(
            """
            SELECT id, username, email, password
            FROM users
            WHERE email = ?
            """,
            (email,)
        ).fetchone()

        conn.close()

        if user and check_password_hash(user['password'], password):
            session['user'] = user['username']
            session['email'] = user['email']
            session['user_id'] = user['id']

            return redirect('/dashboard')

        error = "Invalid email or password"

    return render_template('login.html', error=error)


@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect('/login')

    user_id = session['user_id']

    conn = get_db_connection()

    conn.execute("""
        INSERT INTO workflow_instances (user_id, task_id, current_state)
        SELECT t.user_id, t.id, 'CREATED'
        FROM tasks t
        WHERE t.user_id = ?
        AND NOT EXISTS (
            SELECT 1
            FROM workflow_instances w
            WHERE w.task_id = t.id
                AND w.user_id = t.user_id
        )
    """, (user_id,))

    conn.commit()

    
    tasks = conn.execute(
        """
        SELECT
            t.id,
            t.title,
            t.status,
            (
                SELECT w.current_state
                FROM workflow_instances w
                WHERE w.task_id = t.id
                    AND w.user_id = t.user_id
                ORDER BY w.id DESC
                LIMIT 1
            ) AS workflow_state
        FROM tasks t
        WHERE t.user_id = ?
        ORDER BY t.id DESC
        """,
        (user_id,),
    ).fetchall()


    conn.close()

    score = 0

    for task in tasks:
        if task['status'] == "Completed":
            score += 10
        else:
            score += 2

    return render_template(
        'dashboard.html',
        username=session['user'],
        tasks=tasks,
        score=score
    )


@app.route('/logout')
def logout():
    session.clear()
    return redirect('/login')

init_db()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)