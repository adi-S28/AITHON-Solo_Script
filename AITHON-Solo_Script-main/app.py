from flask import Flask, render_template, request, redirect, url_for, session, jsonify
import pickle
import sqlite3
import pandas as pd
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = 'your_secret_key'  # Replace with your actual secret key

# Load the model and encoder
try:
    with open('fraud_model.pkl', 'rb') as model_file:
        model = pickle.load(model_file)
except (pickle.UnpicklingError, FileNotFoundError) as e:
    print(f"Error loading model: {e}")
    model = None

# Define the columns for features if necessary
feature_columns = ['sender_card_number', 'amount', 'recipient_card_number']
encoder = None  # Replace with actual encoder if available

# Function to connect to the login database
def get_login_db_connection():
    return sqlite3.connect('login.db')

# Function to connect to the payment database
def get_payment_db_connection():
    return sqlite3.connect('database.db')

# Route to initialize the database and create tables if they do not exist
@app.route('/init_db')
def init_db():
    # Initialize payment database
    conn_payment = get_payment_db_connection()
    c_payment = conn_payment.cursor()

    # Create transactions table if not exists
    c_payment.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_card_number TEXT,
            amount TEXT,
            recipient_card_number TEXT,
            prediction INTEGER
        )
    ''')

    # Create cards table if not exists
    c_payment.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT,
            card_number TEXT UNIQUE,
            card_type TEXT,
            expiration_date TEXT,
            cvv TEXT,
            balance REAL
        )
    ''')

    conn_payment.commit()
    conn_payment.close()

    # Initialize login database
    conn_login = get_login_db_connection()
    c_login = conn_login.cursor()

    # Create users table if not exists
    c_login.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT
        )
    ''')

    conn_login.commit()
    conn_login.close()

    return "Database Initialized"

@app.route('/check_db')
def check_db():
    conn = get_login_db_connection()
    c = conn.cursor()
    c.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = c.fetchall()
    conn.close()
    return jsonify(tables)

@app.before_request
def make_session_permanent():
    session.permanent = True
    app.permanent_session_lifetime = timedelta(minutes=3)
    if 'modified_since' not in session:
        session['modified_since'] = datetime.now()

@app.before_request
def check_session_timeout():
    if 'username' in session:
        if datetime.now() - session['modified_since'].replace(tzinfo=None) > app.permanent_session_lifetime:
            session.pop('username', None)
            return redirect(url_for('logout'))

@app.route('/', methods=['GET', 'POST'])
def index():
    if 'username' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        sender_card_number = request.form['sender_card_number']
        amount = float(request.form['amount'])
        recipient_card_number = request.form['recipient_card_number']

        if model is None:
            return redirect(url_for('failure'))

        # Prepare data for prediction
        data = {
            'sender_card_number': sender_card_number,
            'amount': amount,
            'recipient_card_number': recipient_card_number
        }
        input_data = pd.DataFrame([data])

        # Handle missing encoder
        if encoder is not None:
            encoded_data = encoder.transform(input_data)
        else:
            encoded_data = input_data

        prediction = model.predict(encoded_data)

        # Save transaction information to the database
        conn = get_payment_db_connection()
        c = conn.cursor()
        c.execute("INSERT INTO transactions (sender_card_number, amount, recipient_card_number, prediction) VALUES (?, ?, ?, ?)",
                  (sender_card_number, amount, recipient_card_number, int(prediction[0])))

        # Update card balances
        c.execute("UPDATE cards SET balance = balance - ? WHERE card_number = ?", (amount, sender_card_number))
        c.execute("UPDATE cards SET balance = balance + ? WHERE card_number = ?", (amount, recipient_card_number))

        conn.commit()
        conn.close()

        if prediction[0] == 1:
            return redirect(url_for('result', status='fraudulent'))
        else:
            return redirect(url_for('result', status='safe'))

    return render_template('index.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'username' in session:
        return redirect(url_for('index'))

    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = get_login_db_connection()
        c = conn.cursor()
        c.execute("SELECT password FROM users WHERE username = ?", (username,))
        stored_password = c.fetchone()
        conn.close()

        if stored_password and check_password_hash(stored_password[0], password):
            session['username'] = username
            session['modified_since'] = datetime.now()  # Update session timestamp
            return redirect(url_for('index'))
        else:
            return redirect(url_for('failure'))

    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = generate_password_hash(request.form['password'])

        conn = get_login_db_connection()
        c = conn.cursor()
        c.execute("INSERT INTO users (username, password) VALUES (?, ?)",
                  (username, password))
        conn.commit()
        conn.close()
        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/logout')
def logout():
    session.pop('username', None)
    return redirect(url_for('login'))

@app.route('/result/<status>')
def result(status):
    return render_template('result.html', status=status)

@app.route('/failure')
def failure():
    return render_template('failure.html')

@app.route('/insights')
def insights():
    conn = get_payment_db_connection()
    df = pd.read_sql_query("SELECT * FROM transactions", conn)
    conn.close()
    insights = df.groupby('prediction').size()
    return render_template('insights.html', insights=insights)

@app.route('/add_card', methods=['GET', 'POST'])
def add_card():
    if 'username' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        card_number = request.form['card_number']
        card_type = request.form['card_type']
        expiration_date = request.form['expiration_date']
        cvv = request.form['cvv']
        initial_balance = float(request.form['initial_balance'])
        user = session['username']

        conn = get_payment_db_connection()
        c = conn.cursor()
        c.execute("INSERT INTO cards (user, card_number, card_type, expiration_date, cvv, balance) VALUES (?, ?, ?, ?, ?, ?)",
                  (user, card_number, card_type, expiration_date, cvv, initial_balance))
        conn.commit()
        conn.close()

        return redirect(url_for('index'))

    return render_template('add_card.html')

@app.route('/get_card_suggestions', methods=['GET'])
def get_card_suggestions():
    if 'username' not in session:
        return redirect(url_for('login'))

    card_number = request.args.get('card_number', '')

    conn = get_payment_db_connection()
    c = conn.cursor()
    c.execute("SELECT card_number FROM cards WHERE user = ? AND card_number LIKE ?", (session['username'], f'%{card_number}%'))
    suggestions = c.fetchall()
    conn.close()

    return jsonify({'suggestions': [suggestion[0] for suggestion in suggestions]})

if __name__ == '__main__':
    app.run(debug=True)
