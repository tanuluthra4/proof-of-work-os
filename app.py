import os
import sqlite3

from flask import Flask, render_template, request, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash

from config import DB_PATH, SECRET_KEY


app = Flask(__name__)
app.secret_key = SECRET_KEY


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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

    tasks = conn.execute(
        """
        SELECT id, title, status
        FROM tasks
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (user_id,)
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


@app.route('/add-task', methods=['GET', 'POST'])
def add_task():
    if 'user_id' not in session:
        return redirect('/login')

    if request.method == 'POST':
        title = request.form['title']
        status = request.form['status']
        user_id = session['user_id']

        conn = get_db_connection()

        conn.execute(
            """
            INSERT INTO tasks (user_id, title, status)
            VALUES (?, ?, ?)
            """,
            (user_id, title, status)
        )

        conn.commit()
        conn.close()

        return redirect('/dashboard')

    return render_template('add_task.html')


@app.route('/delete-task/<int:task_id>')
def delete_task(task_id):
    if 'user_id' not in session:
        return redirect('/login')

    conn = get_db_connection()

    conn.execute(
        """
        DELETE FROM tasks
        WHERE id = ? AND user_id = ?
        """,
        (task_id, session['user_id'])
    )

    conn.commit()
    conn.close()

    return redirect('/dashboard')


@app.route('/complete-task/<int:task_id>')
def complete_task(task_id):
    if 'user_id' not in session:
        return redirect('/login')

    conn = get_db_connection()

    conn.execute(
        """
        UPDATE tasks
        SET status = 'Completed',
            completed_at = CURRENT_TIMESTAMP
        WHERE id = ? AND user_id = ?
        """,
        (task_id, session['user_id'])
    )

    conn.commit()
    conn.close()

    return redirect('/dashboard')


init_db()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)