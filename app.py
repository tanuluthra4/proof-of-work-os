from flask import Flask, render_template, request, redirect, session
from werkzeug.security import generate_password_hash, check_password_hash
import sqlite3

app = Flask(__name__)
app.secret_key = "pow_os_super_secure_key_2026"


def init_db():
    conn = sqlite3.connect("database/users.db")
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_email TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL
        )
    """)

    conn.commit()
    conn.close()


@app.route('/')
def home():
    return redirect('/login')


@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = generate_password_hash(request.form['password'])

        conn = sqlite3.connect("database/users.db")
        cursor = conn.cursor()

        try:
            cursor.execute(
                "INSERT INTO users (username, email, password) VALUES (?, ?, ?)",
                (username, email, password)
            )
            conn.commit()
            return redirect('/login')
        except:
            return "Email already exists!"

        finally:
            conn.close()

    return render_template('signup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']

        conn = sqlite3.connect("database/users.db")
        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM users WHERE email=?",
            (email,)
        )

        user = cursor.fetchone()
        conn.close()

        if user and check_password_hash(user[3], password):
            session['user'] = user[1]
            session['email'] = user[2]
            return redirect('/dashboard')
        else:
            return "Invalid credentials!"

    return render_template('login.html')


@app.route('/dashboard')
def dashboard():
    if 'user' not in session:
        return redirect('/login')

    user_email = session.get('email')

    conn = sqlite3.connect("database/users.db")
    cursor = conn.cursor()

    cursor.execute(
        "SELECT id, title, status FROM tasks WHERE user_email=?",
        (user_email,)
    )
    tasks = cursor.fetchall()

    score = 0
    for task in tasks:
        if task[2] == "Completed":
            score += 10
        else:
            score += 2

    conn.close()

    return render_template(
        'dashboard.html',
        username=session['user'],
        tasks=tasks,
        score=score
    )


@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/login')

@app.route('/add-task', methods=['GET', 'POST'])
def add_task():
    if 'user' not in session:
        return redirect('/login')

    if request.method == 'POST':
        title = request.form['title']
        status = request.form['status']
        user_email = session.get('email')

        conn = sqlite3.connect("database/users.db")
        cursor = conn.cursor()

        cursor.execute(
            "INSERT INTO tasks (user_email, title, status) VALUES (?, ?, ?)",
            (user_email, title, status)
        )

        conn.commit()
        conn.close()

        return redirect('/dashboard')

    return render_template('add_task.html')


@app.route('/delete-task/<int:task_id>')
def delete_task(task_id):
    if 'user' not in session:
        return redirect('/login')

    conn = sqlite3.connect("database/users.db")
    cursor = conn.cursor()

    cursor.execute("DELETE FROM tasks WHERE id=?", (task_id,))
    conn.commit()
    conn.close()

    return redirect('/dashboard')

@app.route('/complete-task/<int:task_id>')
def complete_task(task_id):
    if 'user' not in session:
        return redirect('/login')

    conn = sqlite3.connect("database/users.db")
    cursor = conn.cursor()

    cursor.execute(
        "UPDATE tasks SET status='Completed' WHERE id=?",
        (task_id,)
    )

    conn.commit()
    conn.close()

    return redirect('/dashboard')

if __name__ == '__main__':
    init_db()
    app.run(debug=True)