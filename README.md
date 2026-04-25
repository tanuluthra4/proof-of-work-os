# Proof of Work OS (POW-OS)

## 🚀 Overview

Proof of Work OS (POW-OS) is a productivity and execution tracking platform built for developers and students to track their real work consistency.
Instead of just claiming skills, users can show proof of execution through task completion, execution score tracking, and personal productivity management.
This project focuses on solving a real problem:
“How do you prove consistency and execution to recruiters?”

---

## 🌐 Live Demo

🔗 Live Application:  
https://proof-of-work-os.onrender.com

---

## 🎯 Problem Statement

Most students say:

- I know Python
- I know DSA
- I am learning development

But recruiters cannot verify execution.

POW-OS solves this by creating a system where users can track actual work completed and generate measurable proof of consistency.

---

## ✨ Features

### 🔐 Authentication System
- User Signup
- User Login
- Secure Password Hashing using Werkzeug
- Session-based Authentication
- Auto-login after Signup
- Inline Error Handling for better UX

### 📋 Task Management
- Add New Tasks
- Delete Tasks
- Mark Tasks as Completed
- Pending / Completed Task Tracking

### 📊 Execution Score System
A custom scoring system to measure productivity:

- Completed Task = +10 points
- Pending Task = +2 points

This creates a visible execution metric for users.

### 🎨 Professional UI
- Custom HTML + CSS + JavaScript UI
- Modern dashboard layout
- Clean task cards
- Smooth user flow
- Product-style interface instead of basic CRUD layout

---

## 🛠 Tech Stack

### Backend
- Python
- Flask
- SQLite

### Frontend
- HTML
- CSS
- JavaScript

### Security
- Werkzeug Password Hashing

### Deployment
- Render (for deployment)

---

## 📂 Project Structure

```text
proof-of-work-os/
│── app.py
│── requirements.txt
│── README.md
│── .gitignore
│
├── database/
│   └── users.db
│
├── templates/
│   ├── login.html
│   ├── signup.html
│   ├── dashboard.html
│   └── add_task.html
│
├── static/
│   ├── css/
│   │   └── style.css
│   │
│   └── js/
│       └── main.js
```

---

## Installation 

Step 1 - Clone Repository
```
git clone https://github.com/tanuluthra4/proof-of-work-os
cd proof-of-work-os
```

Step 2 - Install Dependencies 
```
pip install -r requirements.txt 
```

Step 3 - Run Project 
```
python app.py
```

Step 4 - Open Browser 
```
http://127.0.0.1:5000/
```

---

## Future Improvements 

Planned upgrades:

- Task Edit Feature
- Due Dates
- Priority Levels
- Weekly Productivity Analytics
- Public Shareable Profile
- Recruiter View Dashboard
- GitHub Integration

---

## Author 

Built by Tanu Luthra
Focused on software development, backend systems, and real-world project building.

---

## Final Note

This project is not just a task manager.
It is a proof system for consistency.
Because skills claimed are weak.
Proof of execution is stronger.