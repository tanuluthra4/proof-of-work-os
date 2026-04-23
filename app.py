from flask import Flask, render_template, request, redirect, session
import sqlite3

app = Flask(__name__)
app.secret_key = "your_secret_key"


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
        password = request.form['password']

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
            "SELECT * FROM users WHERE email=? AND password=?",
            (email, password)
        )

        user = cursor.fetchone()
        conn.close()

        if user:
            session['user'] = user[1]
            return redirect('/dashboard')
        else:
            return "Invalid credentials!"

    return render_template('login.html')


@app.route('/dashboard')
def dashboard():
    if 'user' in session:
        return render_template('dashboard.html', username=session['user'])
    return redirect('/login')


@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect('/login')


if __name__ == '__main__':
    init_db()
    app.run(debug=True)