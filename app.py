from flask import Flask, request, jsonify, render_template_string, send_file, session, redirect, url_for, make_response
import requests
import os
import boto3
import json
from datetime import datetime, timedelta
import time
import base64
import logging
import re
import sqlite3
from collections import defaultdict
import csv
from io import StringIO 

# Try to load .env file if it exists (for development/testing)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # dotenv not installed, continue without it

app = Flask(__name__)
app.secret_key = 'blueshift_support_bot_secret_key_2023'
app.permanent_session_lifetime = timedelta(hours=12)

# --- GEMINI API CONFIGURATION ---
AI_API_KEY = os.environ.get('GEMINI_API_KEY')
# Primary model: gemini-1.5-flash (stable and widely available), with fallback to gemini-1.5-pro for reliability
GEMINI_API_URL_PRIMARY = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
GEMINI_API_URL_FALLBACK = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-pro:generateContent"
# ---------------------------------

# AWS Athena configuration - set these via environment variables
ATHENA_DATABASES = os.environ.get('ATHENA_DATABASE', 'customer_campaign_logs').split(',')
ATHENA_S3_OUTPUT = os.environ.get('ATHENA_S3_OUTPUT', 's3://bsft-customers/athena-results/')
AWS_REGION = os.environ.get('AWS_REGION', 'us-west-2')

# API Configuration for searches
JIRA_URL = os.environ.get('JIRA_URL', 'https://blueshift.atlassian.net')
JIRA_TOKEN = os.environ.get('JIRA_TOKEN')
JIRA_EMAIL = os.environ.get('JIRA_EMAIL')

CONFLUENCE_URL = os.environ.get('CONFLUENCE_URL', 'https://blueshift.atlassian.net/wiki')
CONFLUENCE_TOKEN = os.environ.get('CONFLUENCE_TOKEN')
CONFLUENCE_EMAIL = os.environ.get('CONFLUENCE_EMAIL')

ZENDESK_SUBDOMAIN = os.environ.get('ZENDESK_SUBDOMAIN')
ZENDESK_TOKEN = os.environ.get('ZENDESK_TOKEN')
ZENDESK_EMAIL = os.environ.get('ZENDESK_EMAIL')

# Configure logging for production debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Debug environment variables on startup
logger.info(f"Environment variables loaded:")
logger.info(f"JIRA_TOKEN: {'SET' if JIRA_TOKEN else 'NOT SET'}")
logger.info(f"JIRA_EMAIL: {'SET' if JIRA_EMAIL else 'NOT SET'}")
logger.info(f"CONFLUENCE_TOKEN: {'SET' if CONFLUENCE_TOKEN else 'NOT SET'}")
logger.info(f"CONFLUENCE_EMAIL: {'SET' if CONFLUENCE_EMAIL else 'NOT SET'}")
logger.info(f"ZENDESK_TOKEN: {'SET' if ZENDESK_TOKEN else 'NOT SET'}")
logger.info(f"ZENDESK_SUBDOMAIN: {'SET' if ZENDESK_SUBDOMAIN else 'NOT SET'}")


# --- FIX 2: Add Credential Validation at Startup ---
def validate_api_credentials_on_startup():
    """Test all API credentials at startup and log results"""
    validation_results = {}

    # Test JIRA
    if JIRA_TOKEN and JIRA_EMAIL and JIRA_URL:
        try:
            auth = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
            headers = {'Authorization': f'Basic {auth}', 'Accept': 'application/json'}
            response = requests.get(f"{JIRA_URL}/rest/api/3/myself", headers=headers, timeout=10)
            validation_results['jira'] = response.status_code == 200
            if response.status_code != 200:
                logger.error(f"JIRA validation failed: {response.status_code} - {response.text[:200]}")
        except Exception as e:
            validation_results['jira'] = False
            logger.error(f"JIRA validation exception: {e}")
    else:
        validation_results['jira'] = False
        logger.error("JIRA credentials missing")

    # Test Confluence
    if CONFLUENCE_TOKEN and CONFLUENCE_EMAIL and CONFLUENCE_URL:
        try:
            response = requests.get(
                f"{CONFLUENCE_URL}/rest/api/user/current",
                auth=(CONFLUENCE_EMAIL, CONFLUENCE_TOKEN),
                timeout=10
            )
            validation_results['confluence'] = response.status_code == 200
            if response.status_code != 200:
                logger.error(f"Confluence validation failed: {response.status_code}")
        except Exception as e:
            validation_results['confluence'] = False
            logger.error(f"Confluence validation exception: {e}")
    else:
        validation_results['confluence'] = False
        logger.error("Confluence credentials missing")

    # Test Zendesk
    if ZENDESK_TOKEN and ZENDESK_EMAIL and ZENDESK_SUBDOMAIN:
        try:
            auth = base64.b64encode(f"{ZENDESK_EMAIL}/token:{ZENDESK_TOKEN}".encode()).decode()
            headers = {'Authorization': f'Basic {auth}', 'Accept': 'application/json'}
            response = requests.get(
                f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/users/me.json",
                headers=headers,
                timeout=10
            )
            validation_results['zendesk'] = response.status_code == 200
            if response.status_code != 200:
                logger.error(f"Zendesk validation failed: {response.status_code}")
        except Exception as e:
            validation_results['zendesk'] = False
            logger.error(f"Zendesk validation exception: {e}")
    else:
        validation_results['zendesk'] = False
        logger.error("ZENDESK credentials missing")

    logger.info(f"🔍 API Validation Results: {validation_results}")
    return validation_results

# Execute validation on startup
API_STATUS = validate_api_credentials_on_startup()
# --- END FIX 2 ---


# --- AGENT ACTIVITY LOGGING DATABASE ---
# Use /data directory if it exists (Railway persistent volume), otherwise use current directory
DATA_DIR = '/data' if os.path.exists('/data') else '.'
DB_PATH = os.path.join(DATA_DIR, 'agent_activity.db')
logger.info(f"Database path: {DB_PATH}")

def init_activity_db():
    """Initialize the activity logging database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS activity_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            query_text TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            response_status TEXT,
            resources_found INTEGER DEFAULT 0,
            athena_used BOOLEAN DEFAULT 0
        )
    ''')
    conn.commit()
    conn.close()
    logger.info("Activity logging database initialized")

def log_agent_activity(agent_name, query_text, response_status='success', resources_found=0, athena_used=False):
    """Log an agent's query activity"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO activity_logs (agent_name, query_text, response_status, resources_found, athena_used)
            VALUES (?, ?, ?, ?, ?)
        ''', (agent_name, query_text, response_status, resources_found, athena_used))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Error logging activity: {e}")

def get_activity_stats(days=30):
    """Get activity statistics for dashboard"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()

        # Get date range
        date_threshold = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d %H:%M:%S')

        # Total queries by agent
        cursor.execute('''
            SELECT agent_name, COUNT(*) as query_count
            FROM activity_logs
            WHERE timestamp >= ?
            GROUP BY agent_name
            ORDER BY query_count DESC
        ''', (date_threshold,))
        queries_by_agent = cursor.fetchall()

        # Recent activity (last 50)
        cursor.execute('''
            SELECT agent_name, query_text, timestamp, response_status
            FROM activity_logs
            ORDER BY timestamp DESC
            LIMIT 50
        ''')
        recent_activity = cursor.fetchall()

        # Daily activity trends
        cursor.execute('''
            SELECT DATE(timestamp) as date, COUNT(*) as count
            FROM activity_logs
            WHERE timestamp >= ?
            GROUP BY DATE(timestamp)
            ORDER BY date DESC
        ''', (date_threshold,))
        daily_trends = cursor.fetchall()

        # Top search topics (simple keyword extraction)
        cursor.execute('''
            SELECT query_text
            FROM activity_logs
            WHERE timestamp >= ?
        ''', (date_threshold,))
        all_queries = cursor.fetchall()

        # Monthly trends by agent (last 6 months)
        six_months_ago = (datetime.now() - timedelta(days=180)).strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
            SELECT
                strftime('%Y-%m', timestamp) as month,
                agent_name,
                COUNT(*) as count
            FROM activity_logs
            WHERE timestamp >= ?
            GROUP BY month, agent_name
            ORDER BY month DESC, count DESC
        ''', (six_months_ago,))
        monthly_by_agent = cursor.fetchall()

        # Process monthly data for visualization
        monthly_data = {}
        for month, agent, count in monthly_by_agent:
            if month not in monthly_data:
                monthly_data[month] = {}
            monthly_data[month][agent] = count

        conn.close()

        return {
            'queries_by_agent': queries_by_agent,
            'recent_activity': recent_activity,
            'daily_trends': daily_trends,
            'all_queries': [q[0] for q in all_queries],
            'monthly_by_agent': monthly_by_agent,
            'monthly_data': monthly_data
        }
    except Exception as e:
        logger.error(f"Error getting activity stats: {e}")
        return None

def delete_agent_entries(agent_name):
    """Delete all entries for a specific agent"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM activity_logs WHERE agent_name = ?', (agent_name,))
        deleted_count = cursor.rowcount
        conn.commit()
        conn.close()
        return deleted_count
    except Exception as e:
        logger.error(f"Error deleting agent entries: {e}")
        return 0

def export_all_queries():
    """Export all query data for CSV download"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
            SELECT agent_name, query_text, timestamp, response_status, resources_found, athena_used
            FROM activity_logs
            ORDER BY timestamp DESC
        ''')
        all_data = cursor.fetchall()
        conn.close()
        return all_data
    except Exception as e:
        logger.error(f"Error exporting queries: {e}")
        return []

# Initialize database on startup
init_activity_db()
# --- END AGENT ACTIVITY LOGGING ---


# --- REPLACEMENT FOR call_anthropic_api, WITH AI RESPONSE FIX ---
def call_gemini_api(query, platform_resources=None, temperature=0.2):
    """Call Google Gemini API with system context and configuration.
    
    FIX: Max output tokens increased to 4000 to prevent MAX_TOKENS error.
    """
    if not AI_API_KEY:
        return "Error: GEMINI_API_KEY is not configured."

    try:
        headers = {
            'Content-Type': 'application/json',
        }
        
        # Build context from actual retrieved content
        platform_context = ""

        if platform_resources and len(platform_resources) > 0:
            resources_with_content = [r for r in platform_resources if isinstance(r, dict) and 'content' in r and len(r.get('content', '').strip()) > 50]

            if resources_with_content:
                platform_context = "\n\nDOCUMENTATION CONTENT FROM SEARCH RESULTS:\n"
                for i, resource in enumerate(resources_with_content[:4]):
                    platform_context += f"\n=== SOURCE {i+1}: {resource['title']} ===\n"
                    platform_context += f"URL: {resource['url']}\n"
                    platform_context += f"CONTENT:\n{resource['content'][:2000]}\n"
                    platform_context += "="*50 + "\n"
            else:
                platform_context = "\n\nRELEVANT RESOURCES FOUND (URLs only):\n"
                for i, resource in enumerate(platform_resources[:3]):
                    platform_context += f"{i+1}. {resource.get('title', 'Untitled')}\n   URL: {resource.get('url', 'N/A')}\n"

        # System Instruction content
        # FIX: Temperature set to 0.3 for the general query to reduce brittleness/conversational stops.
        if temperature > 0.0:
            temp = 0.3
        else:
            temp = temperature

        system_instruction_content = f"""You are Blueshift support helping troubleshoot customer issues. Your response MUST be comprehensive, actionable, and formatted using Markdown with proper bold formatting for readability.

INSTRUCTIONS:
1. **PRIORITY 1: Platform Navigation Steps.** Extract clear, numbered steps from the documentation content if available.
2. If documentation content contains step-by-step instructions, use them precisely.
3. If no specific steps in docs, provide general navigation based on Blueshift platform knowledge.
4. Combine documentation steps with your Blueshift platform knowledge for comprehensive guidance.
5. Focus on practical troubleshooting guidance.
6. **CRITICAL: Use bold markdown (**text**) for all section headers, key terms, important UI elements, menu paths, and button names to improve readability.**

RESPONSE FORMAT:

## **Feature Overview**
Explain what this feature/issue is about and how it relates to the Blueshift platform. Use **bold** for key concepts and feature names.

## **Platform Navigation Steps**
Based on the documentation above and Blueshift platform knowledge:

1. Navigate to **Menu Name** → **Submenu** → **Feature**
2. Use **bold** for all button names, field labels, and UI elements
3. Include specific **menu paths**, **button names**, and navigation instructions in bold

## **Troubleshooting Steps**
When this feature isn't working as expected:

1. **Platform Configuration Checks**
   - **Verify settings and required fields** - use bold for key actions
   - **Check user permissions and access**
   - **Confirm campaign/trigger status**

2. **Common Issues and Solutions**
   - **Typical problems** and their fixes (bold the problem type)
   - **Configuration errors** to look for
   - **Data flow issues** to investigate

3. **Advanced Debugging**
   - **Database queries:** customer_campaign_logs.campaign_execution_v3
   - **Error patterns:** ExternalFetchError, ChannelLimitError, DeduplicationError
   - **API endpoints** to test

## **Internal Notes**
- **Main troubleshooting database:** customer_campaign_logs.campaign_execution_v3
- **API Base:** https://api.getblueshift.com
- This is internal support guidance - provide actionable troubleshooting steps

FORMATTING RULES:
- Use **bold** for ALL section headers (even though they're already ## markdown headers)
- Use **bold** for key terms, concepts, feature names, and technical terms
- Use **bold** for UI elements: buttons, menus, fields, tabs, etc.
- Use **bold** for error types, status names, and system messages
- Use **bold** for database names, table names, and API endpoints
- This makes the response much easier to scan and read
"""

        # FIX: Ensure the prompt explicitly tells the model to start the structured response
        start_instruction = "Start your response immediately using the RESPONSE FORMAT provided below. Do NOT use conversational filler like 'Of course, here is...'"
        full_prompt = system_instruction_content + "\n\n" + start_instruction + "\n\n---\n\nSUPPORT QUERY: " + query + "\n" + platform_context

        contents_array = [
            {"role": "user", "parts": [{"text": full_prompt}]}
        ]

        data = {
            "contents": contents_array,
            "generationConfig": { 
                "temperature": temp, 
                "maxOutputTokens": 4000  # CRITICAL FIX: Increased capacity
            }
        }
        
        # Try primary model (1.5 Flash) first, then fallback to 1.5 Pro
        models_to_try = [
            ("Gemini 1.5 Flash", GEMINI_API_URL_PRIMARY),
            ("Gemini 1.5 Pro", GEMINI_API_URL_FALLBACK)
        ]

        for model_name, model_url in models_to_try:
            url_with_key = f"{model_url}?key={AI_API_KEY}"

            # Retry logic for 503 errors (model overloaded)
            max_retries = 2 if model_name == "Gemini 1.5 Flash" else 1  # Only retry Flash
            retry_delay = 1  # seconds

            for attempt in range(max_retries):
                try:
                    # Timeout increased to 60 seconds
                    response = requests.post(url_with_key, headers=headers, json=data, timeout=60)

                    if response.status_code == 200:
                        response_json = response.json()
                        # Safely extract the text from the response structure
                        gemini_response = response_json.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()
                        if not gemini_response:
                            # Check for prompt filtering or safety block issues
                            return f"API Error: Response blocked or empty. Reason: {response_json.get('candidates', [{}])[0].get('finishReason')}"

                        # Log which model was used
                        if model_name == "Gemini 2.5 Pro":
                            logger.info("✓ Response generated using fallback model (Gemini 2.5 Pro)")
                        else:
                            logger.info("✓ Response generated using primary model (Gemini 2.5 Flash)")

                        return gemini_response

                    elif response.status_code == 503:
                        if attempt < max_retries - 1:
                            # Model overloaded - retry with exponential backoff
                            wait_time = retry_delay * (2 ** attempt)
                            logger.warning(f"{model_name} API 503 (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time}s...")
                            time.sleep(wait_time)
                            continue
                        else:
                            # Max retries reached for this model, try next model
                            logger.warning(f"{model_name} unavailable (503). Trying fallback model...")
                            break
                    else:
                        # Other error - don't retry, try fallback model
                        error_body = response.text[:500] if hasattr(response, 'text') else 'No error body'
                        logger.warning(f"{model_name} error {response.status_code}: {error_body}. Trying fallback model...")
                        break

                except requests.exceptions.Timeout:
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.warning(f"{model_name} timeout (attempt {attempt + 1}/{max_retries}). Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.warning(f"{model_name} timed out. Trying fallback model...")
                        break

        # If we get here, both models failed
        return "API Error: Both primary and fallback models unavailable. Please try again later."

    except Exception as e:
        return f"Error: {str(e)}"
# --- END REPLACEMENT ---


def call_gemini_api_with_conversation(followup_query, original_query, original_response, platform_resources=None):
    """Call Gemini API with conversation context for follow-up questions.

    This maintains the conversation thread by including the original query and response,
    allowing the AI to provide contextually relevant follow-up answers.
    """
    if not AI_API_KEY:
        return "Error: GEMINI_API_KEY is not configured."

    try:
        headers = {
            'Content-Type': 'application/json',
        }

        # Build context from platform resources if available
        platform_context = ""
        if platform_resources and len(platform_resources) > 0:
            resources_with_content = [r for r in platform_resources if isinstance(r, dict) and 'content' in r and len(r.get('content', '').strip()) > 50]

            if resources_with_content:
                platform_context = "\n\nRELEVANT DOCUMENTATION:\n"
                for i, resource in enumerate(resources_with_content[:3]):
                    platform_context += f"\n=== {resource['title']} ===\n"
                    platform_context += f"{resource['content'][:1500]}\n"

        # System instruction for conversational follow-ups
        system_instruction = """You are Blueshift support assistant helping troubleshoot customer issues. You are now answering a FOLLOW-UP question based on a previous conversation.

INSTRUCTIONS:
1. Reference the original conversation context provided below
2. Provide a direct, focused answer to the follow-up question
3. Use **bold markdown** for key terms, UI elements, and important concepts
4. Keep the response conversational but informative
5. If clarifying previous information, acknowledge what was discussed before
6. Provide additional details, examples, or clarifications as requested

FORMATTING:
- Use markdown formatting with **bold** for emphasis
- Use numbered lists for steps
- Use bullet points for options or features
- Be concise but thorough"""

        # Build the conversation history
        conversation_prompt = f"""{system_instruction}

---
PREVIOUS CONVERSATION:

Original Question: {original_query}

My Previous Response: {original_response[:1500]}

---
FOLLOW-UP QUESTION: {followup_query}
{platform_context}

Please provide a focused answer to the follow-up question, referencing the previous conversation as needed."""

        contents_array = [
            {"role": "user", "parts": [{"text": conversation_prompt}]}
        ]

        data = {
            "contents": contents_array,
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 3000
            }
        }

        # Try primary model first, then fallback
        models_to_try = [
            ("Gemini 1.5 Flash", GEMINI_API_URL_PRIMARY),
            ("Gemini 1.5 Pro", GEMINI_API_URL_FALLBACK)
        ]

        for model_name, model_url in models_to_try:
            url_with_key = f"{model_url}?key={AI_API_KEY}"

            max_retries = 2 if model_name == "Gemini 1.5 Flash" else 1
            retry_delay = 1

            for attempt in range(max_retries):
                try:
                    logger.info(f"Calling {model_name} for follow-up (attempt {attempt + 1})")
                    response = requests.post(url_with_key, headers=headers, json=data, timeout=30)

                    if response.status_code == 200:
                        response_json = response.json()
                        text = response_json.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()

                        if text:
                            logger.info(f"{model_name} follow-up response successful")
                            return text
                        else:
                            logger.warning(f"{model_name} returned empty text")
                            break

                    elif response.status_code == 503:
                        if attempt < max_retries - 1:
                            wait_time = retry_delay * (2 ** attempt)
                            logger.warning(f"{model_name} overloaded. Retrying in {wait_time}s...")
                            time.sleep(wait_time)
                            continue
                        else:
                            logger.warning(f"{model_name} unavailable. Trying fallback...")
                            break
                    else:
                        logger.error(f"{model_name} error {response.status_code}: {response.text[:200]}")
                        break

                except requests.Timeout:
                    if attempt < max_retries - 1:
                        wait_time = retry_delay * (2 ** attempt)
                        logger.warning(f"{model_name} timeout. Retrying in {wait_time}s...")
                        time.sleep(wait_time)
                        continue
                    else:
                        logger.warning(f"{model_name} timed out. Trying fallback...")
                        break

        return "API Error: Unable to process follow-up question. Please try again later."

    except Exception as e:
        logger.error(f"Error in conversational follow-up: {e}")
        return f"Error: {str(e)}"


def generate_followup_suggestions(original_query, ai_response):
    """Generate 3 relevant follow-up questions based on the query and response."""
    if not AI_API_KEY:
        logger.warning("No AI API key - returning default follow-up suggestions")
        return get_default_followup_suggestions(original_query)

    try:
        logger.info(f"Generating follow-up suggestions for query: {original_query[:50]}...")

        prompt = f"""Based on this support query and response, generate exactly 3 short, relevant follow-up questions that a user might want to ask next.

ORIGINAL QUERY: {original_query}

AI RESPONSE: {ai_response[:1000]}

Generate 3 natural follow-up questions that:
1. Ask for more specific details or clarification
2. Explore related features or troubleshooting steps
3. Request practical examples or best practices

Format your response as ONLY 3 questions, one per line, with no numbering, bullets, or extra text.
Each question should be concise (max 10 words).

Example format:
How do I configure this in the UI?
What are common errors with this feature?
Can you show me an example implementation?"""

        headers = {'Content-Type': 'application/json'}
        contents_array = [{"role": "user", "parts": [{"text": prompt}]}]
        data = {
            "contents": contents_array,
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 300
            }
        }

        url_with_key = f"{GEMINI_API_URL_PRIMARY}?key={AI_API_KEY}"
        response = requests.post(url_with_key, headers=headers, json=data, timeout=15)

        if response.status_code == 200:
            response_json = response.json()
            text = response_json.get('candidates', [{}])[0].get('content', {}).get('parts', [{}])[0].get('text', '').strip()

            logger.info(f"Follow-up API response: {text[:200]}")

            if text:
                # Parse the response into individual questions
                questions = [q.strip() for q in text.split('\n') if q.strip() and len(q.strip()) > 10]
                # Return up to 3 questions
                if len(questions) > 0:
                    logger.info(f"Generated {len(questions)} follow-up suggestions")
                    return questions[:3]

        logger.warning(f"API returned no valid follow-up suggestions, using defaults")
        return get_default_followup_suggestions(original_query)

    except Exception as e:
        logger.error(f"Error generating follow-up suggestions: {e}")
        return get_default_followup_suggestions(original_query)


def get_default_followup_suggestions(query):
    """Return default follow-up suggestions when AI generation fails."""
    return [
        "How do I configure this in the platform?",
        "What are common errors with this feature?",
        "Can you show me troubleshooting steps?"
    ]


# --- FIX: JIRA Search - Switched to GET request for reliability ---
def search_jira_tickets_improved(query, limit=5, debug=True):
    """FIXED: Switched JIRA search from POST to GET with JQL in params for higher reliability, avoiding 410 error."""
    try:
        if not API_STATUS.get('jira', False):
            logger.warning("JIRA API not available - skipping search")
            return []

        # Same stop words as Confluence
        STOP_WORDS = {'why', 'is', 'my', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'how', 'what', 'when', 'where', 'who'}

        def clean_words(words):
            """Remove stop words and short words"""
            return [w for w in words if len(w) > 2 and w.lower() not in STOP_WORDS]

        auth = base64.b64encode(f"{JIRA_EMAIL}:{JIRA_TOKEN}".encode()).decode()
        headers = {
            'Authorization': f'Basic {auth}',
            'Accept': 'application/json',
        }

        # --- Clean query words ---
        words = query.strip().split()
        clean_query_words = clean_words(words)

        if not clean_query_words:
            clean_query_words = words

        logger.info(f"JIRA search - Original: '{query}' -> Clean words: {clean_query_words}")

        # --- Build JQL queries progressively for GET request ---
        jql_variants = []

        # 1. Exact phrase (highest relevance)
        jql_variants.append(f'summary ~ "\\"{query}\\"" ORDER BY updated DESC')

        # 2. All clean words AND in summary and text (better relevance than OR)
        if len(clean_query_words) > 1:
            and_parts = [f'(summary ~ "{w}" OR text ~ "{w}")' for w in clean_query_words]
            jql_variants.append(f'({" AND ".join(and_parts)}) ORDER BY updated DESC')

        # 3. Most important words (fallback) - use the two longest/most significant words
        if len(clean_query_words) >= 2:
            important_words = sorted(clean_query_words, key=len, reverse=True)[:2]
            important_parts = [f'(summary ~ "{w}" OR text ~ "{w}")' for w in important_words]
            jql_variants.append(f'({" AND ".join(important_parts)}) ORDER BY updated DESC')

        # 4. Clean words OR in summary and text (most reliable for finding results but least relevant)
        or_parts = [f'(summary ~ "{w}" OR text ~ "{w}")' for w in clean_query_words]
        jql_variants.append(f'({" OR ".join(or_parts)}) ORDER BY updated DESC')

        # Use the correct JIRA v3 API endpoint for JQL queries
        url = f"{JIRA_URL}/rest/api/3/search/jql"

        # --- Try queries in order ---
        final_issues = []
        for i, jql in enumerate(jql_variants):
            try:
                logger.info(f"Trying JIRA JQL #{i+1} (GET): {jql}")

                params = {
                    'jql': jql,
                    'maxResults': limit * 3,
                    'fields': 'summary,key,status,priority,issuetype'
                }

                # Use the correct v3 API endpoint with GET request
                response = requests.get(url, headers=headers, params=params, timeout=15) 

                if response.status_code == 200:
                    data = response.json()
                    issues = data.get('issues', [])

                    if issues:
                        logger.info(f"JIRA query #{i+1} returned {len(issues)} results. Breaking.")
                        final_issues = issues
                        break
                    else:
                        logger.info(f"JIRA query #{i+1} returned no results")
                else:
                    # Log the failed JIRA endpoint search error
                    logger.error(f"JIRA API error on query #{i+1} (GET): {response.status_code} - {response.text[:200]}")

            except Exception as e:
                logger.error(f"JIRA query #{i+1} failed: {e}")
                continue

        if not final_issues:
            logger.info("No JIRA results found with any query variant")
            return []

        # --- Score and filter results ---
        def score_issue(issue):
            summary = issue.get('fields', {}).get('summary', '').lower()

            # Count partial word matches in summary (more lenient)
            matches = sum(1 for word in clean_query_words if word.lower() in summary)

            # Bonus for exact phrase match
            exact_bonus = 50 if query.lower() in summary else 0

            # Bonus for multiple word matches (AND logic preference)
            if len(clean_query_words) > 1:
                match_ratio = matches / len(clean_query_words)
                completeness_bonus = int(match_ratio * 20)  # Up to 20 points for having all words
            else:
                completeness_bonus = 0

            # Priority bonus (less important than relevance)
            priority = issue.get('fields', {}).get('priority', {})
            priority_name = priority.get('name', '').lower() if priority else ''
            priority_bonus = 3 if 'high' in priority_name or 'critical' in priority_name else 0

            # Issue type bonus (prefer certain types)
            issue_type = issue.get('fields', {}).get('issuetype', {})
            issue_type_name = issue_type.get('name', '').lower() if issue_type else ''
            type_bonus = 5 if 'bug' in issue_type_name or 'support' in issue_type_name else 0

            # Minimum base score for relevant-sounding tickets
            base_score = 5 if ('facebook' in summary or 'syndication' in summary or 'audience' in summary) else 0

            total_score = (matches * 10) + exact_bonus + completeness_bonus + priority_bonus + type_bonus + base_score
            return total_score

        # Sort by relevance score
        scored_issues = [(score_issue(issue), issue) for issue in final_issues]
        scored_issues.sort(reverse=True, key=lambda x: x[0])

        # Debug log top scoring issues
        logger.info(f"JIRA scoring results:")
        for i, (score, issue) in enumerate(scored_issues[:5]):
            summary = issue.get('fields', {}).get('summary', 'No summary')
            key = issue.get('key', 'Unknown')
            logger.info(f"  {i+1}. Score: {score} - {key}: {summary[:50]}...")

        # --- Format results ---
        results = []
        for score, issue in scored_issues[:limit]:
            summary = issue.get('fields', {}).get('summary', 'No summary')
            key = issue.get('key', 'Unknown')
            results.append({
                'title': f"{key}: {summary}",
                'url': f"{JIRA_URL}/browse/{key}"
            })

        logger.info(f"JIRA search found {len(results)} relevant results")
        return results

    except Exception as e:
        logger.error(f"JIRA search error: {e}")
        return []
# --- END FIX ---

# --- FIX: Confluence Search - Bypassed Validation for Raw Results ---
def search_confluence_docs_improved(query, limit=5, space_key=None, debug=True):
    """
    FIXED: Confluence search logic. Returns raw results, relying on central validation.
    """
    try:
        if not API_STATUS.get('confluence', False):
            logger.warning("Confluence API not available - skipping search")
            return []

        # Stop words that break Confluence CQL
        STOP_WORDS = {'why', 'is', 'my', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'how', 'what', 'when', 'where', 'who'}

        def clean_words(words):
            """Remove stop words and short words"""
            return [w for w in words if len(w) > 2 and w.lower() not in STOP_WORDS]

        def run_search(cql):
            url = f"{CONFLUENCE_URL}/rest/api/content/search"
            params = {
                "cql": cql,
                "limit": limit * 10,   # pull more for debugging
                "expand": "content"
            }
            resp = requests.get(url, params=params, auth=(CONFLUENCE_EMAIL, CONFLUENCE_TOKEN), timeout=15)
            resp.raise_for_status()
            return resp.json().get("results", [])

        # --- Clean query words ---
        words = query.strip().split()
        clean_query_words = clean_words(words)

        # If we filtered out everything, use original words
        if not clean_query_words:
            clean_query_words = words

        logger.info(f"Original query: '{query}' -> Clean words: {clean_query_words}")

        # --- Build queries progressively ---
        cql_variants = []

        # 1. Exact phrase (standard fields)
        cql_variants.append(f'text ~ "\\"{query}\\"" OR title ~ "\\"{query}\\""')

        # 2. Clean words AND (standard fields)
        if len(clean_query_words) > 1:
            and_parts = [f'(title ~ "{w}" OR text ~ "{w}")' for w in clean_query_words]
            cql_variants.append(" AND ".join(and_parts))

        # 3. Clean words OR (standard fields)
        or_parts = [f'(title ~ "{w}" OR text ~ "{w}")' for w in clean_query_words]
        or_parts.append(f'content ~ "{query}"')
        cql_variants.append(" OR ".join(or_parts))


        # 4. Single most important word (if we have multiple)
        if len(clean_query_words) > 1:
            # Use longest word as most likely to be significant
            main_word = max(clean_query_words, key=len)
            cql_variants.append(f'title ~ "{main_word}" OR text ~ "{main_word}"')

        # Add space filter if provided
        if space_key:
            cql_variants = [f'space.key = "{space_key}" AND ({c})' for c in cql_variants]

        # --- Try queries in order ---
        final_results = []
        for i, cql in enumerate(cql_variants):
            try:
                logger.info(f"Trying Confluence CQL #{i+1}: {cql}")
                results = run_search(cql)

                if results:
                    final_results = results
                    logger.info(f"Query #{i+1} returned {len(results)} results. Using these results.")
                    break
                else:
                    logger.info(f"Query #{i+1} returned no results, trying next query...")

            except requests.exceptions.HTTPError as http_e:
                 logger.error(f"Confluence query #{i+1} failed HTTP: {http_e.response.status_code} - {http_e.response.text[:100]}", exc_info=True)
                 continue
            except Exception as e:
                logger.error(f"Confluence query #{i+1} failed: {e}", exc_info=True)
                continue

        if not final_results:
             logger.info("No Confluence results found with any query variant")
             return []

        # --- Re-rank: trust API score, tiny title nudge ---
        def score_fn(r):
            api_score = r.get("score", 0) or 0
            title = (r.get("title") or "").lower()

            # Check if any clean words appear in title
            title_word_matches = sum(1 for word in clean_query_words if word.lower() in title)
            boost = title_word_matches * 5  # Small boost per matching word

            return api_score * 100 + boost

        ranked = sorted(final_results, key=score_fn, reverse=True)

        # --- Format results ---
        formatted = []
        for r in ranked[:limit]:
            # Try multiple ways to get the page ID due to different Confluence API response formats
            page_id = r.get("content", {}).get("id") or r.get("id")
            title = r.get("title") or "Untitled"

            # Debug log the structure of results that don't have page_id
            if not page_id:
                logger.warning(f"Confluence result missing page_id: {r.keys()} - title: {title}")
                # Try to construct URL using other fields if available
                if "url" in r:
                    page_url = r["url"]
                elif "_links" in r and "webui" in r["_links"]:
                    page_url = f"{CONFLUENCE_URL.rstrip('/wiki')}{r['_links']['webui']}"
                else:
                    continue  # Skip if we can't get a URL
            else:
                # Use the most common and reliable URL format
                page_url = f"{CONFLUENCE_URL}/pages/viewpage.action?pageId={page_id}"

            formatted.append({"title": title, "url": page_url})

        logger.info(f"Confluence search found {len(formatted)} results")
        
        # Returning raw results to be validated centrally in generate_related_resources_improved
        return formatted
    
    except Exception as e:
        logger.error(f"Confluence search error: {e}", exc_info=True)
        return []
# --- END FIX ---


# --- FIX 3: Simplified search_zendesk_tickets (Kept same) ---
def search_zendesk_tickets_improved(query, limit=5):
    """Simplified Zendesk search, using API_STATUS"""
    if not API_STATUS.get('zendesk', False):
        logger.warning("Zendesk API not available - skipping search")
        return []

    try:
        # Assuming token auth is used via ZENDESK_EMAIL/token
        auth = base64.b64encode(f"{ZENDESK_EMAIL}/token:{ZENDESK_TOKEN}".encode()).decode()
        headers = {
            'Authorization': f'Basic {auth}',
            'Accept': 'application/json'
        }

        url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/search.json"
        params = {
            'query': f'({query}) type:ticket',
            'per_page': limit,
            'sort_by': 'updated_at',
            'sort_order': 'desc'
        }

        response = requests.get(url, headers=headers, params=params, timeout=20)

        if response.status_code == 200:
            data = response.json()
            results = []
            
            for ticket in data.get('results', []):
                subject = ticket.get('subject', 'No Subject')
                ticket_id = ticket.get('id')
                
                results.append({
                    'title': f"Ticket #{ticket_id}: {subject}",
                    'url': f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/agent/tickets/{ticket_id}"
                })
            
            logger.info(f"Zendesk search returned {len(results)} results for '{query}'")
            return results
        else:
            logger.error(f"ZENDESK search failed: {response.status_code} - {response.text[:200]}")
            return []
            
    except Exception as e:
        logger.error(f"Zendesk search exception: {e}")
        return []
# --- END FIX 3 ---


def search_help_docs(query, limit=3):
    """IMPROVED help docs search with better trigger/mobile coverage (using curated list as fallback)"""
    try:
        # Try API search first
        if ZENDESK_SUBDOMAIN and ZENDESK_TOKEN and API_STATUS.get('zendesk', False):
            if ZENDESK_EMAIL:
                auth = base64.b64encode(f"{ZENDESK_EMAIL}/token:{ZENDESK_TOKEN}".encode()).decode()
                headers = {
                    'Authorization': f'Basic {auth}',
                    'Accept': 'application/json'
                }
            else:
                headers = {
                    'Authorization': f'Bearer {ZENDESK_TOKEN}',
                    'Accept': 'application/json'
                }

            search_url = f"https://{ZENDESK_SUBDOMAIN}.zendesk.com/api/v2/help_center/articles/search.json"
            response = requests.get(search_url, headers=headers, params={
                'query': query,
                'per_page': 8  # Get more results
            }, timeout=15)

            if response.status_code == 200:
                data = response.json()
                results = []
                for article in data.get('results', []):
                    title = article.get('title', 'Untitled')
                    url = article.get('html_url', '')
                    if title and url:
                        results.append({'title': title, 'url': url})

                if results:
                    logger.info(f"Help Center API found {len(results)} results")
                    return results[:limit]
            else:
                 logger.warning(f"Zendesk Help Center API failed: {response.status_code}")

    except Exception as e:
        logger.error(f"Help Center API search error: {e}")

    # EXPANDED curated list logic (kept as solid fallback)
    help_docs_expanded = [
        {"title": "Campaign Studio - Journey Tab & Detail Mode", "url": "https://help.blueshift.com/hc/en-us/articles/4408704180499-Campaign-studio", "keywords": ["campaign", "studio", "journey", "detail", "mode", "trigger", "troubleshoot", "filter", "conditions", "navigation"]},
        {"title": "User Journey in Campaign - Trigger Troubleshooting", "url": "https://help.blueshift.com/hc/en-us/articles/4408704006675-User-journey-in-a-campaign", "keywords": ["user", "journey", "trigger", "troubleshoot", "not", "sending", "evaluation", "filter", "conditions"]},
        {"title": "Triggered Campaigns - Setup and Configuration", "url": "https://help.blueshift.com/hc/en-us/articles/4405437140115-Triggered-workflows", "keywords": ["triggered", "campaigns", "workflows", "configuration", "setup", "automation", "troubleshoot", "not", "working"]},
        {"title": "Event Triggered Campaigns", "url": "https://help.blueshift.com/hc/en-us/articles/360050760774-Transactions-in-event-triggered-campaigns", "keywords": ["event", "triggered", "campaigns", "transactions", "setup", "troubleshoot", "not", "firing"]},
        {"title": "Trigger Actions and Conditions", "url": "https://help.blueshift.com/hc/en-us/articles/4408725448467-Trigger-Actions", "keywords": ["trigger", "actions", "conditions", "platform", "navigation", "check", "edit", "setup"]},
        {"title": "Campaign Flow Control and Filters", "url": "https://help.blueshift.com/hc/en-us/articles/4408717301651-Campaign-flow-control", "keywords": ["flow", "control", "filters", "conditions", "trigger", "exit", "journey", "not", "working"]},
        {"title": "Journey Testing and Debugging", "url": "https://help.blueshift.com/hc/en-us/articles/4408718647059-Journey-testing", "keywords": ["journey", "testing", "troubleshoot", "debug", "trigger", "not", "working", "preview", "test"]},
        {"title": "Campaign Execution and Troubleshooting", "url": "https://help.blueshift.com/hc/en-us/articles/19600265288979-Campaign-execution-overview", "keywords": ["campaign", "execution", "troubleshoot", "trigger", "not", "sending", "issues", "monitoring"]},
        {"title": "Mobile Push Notifications", "url": "https://help.blueshift.com/hc/en-us/articles/115002714413-Push-notifications", "keywords": ["mobile", "push", "notifications", "app", "trigger", "cloud", "messaging", "setup"]},
        {"title": "In-App Messages Setup", "url": "https://help.blueshift.com/hc/en-us/articles/360043199611-In-app-messages", "keywords": ["in-app", "messages", "mobile", "app", "trigger", "cloud", "setup", "configuration"]},
        {"title": "Mobile SDK Integration", "url": "https://help.blueshift.com/hc/en-us/articles/360043199451-Mobile-SDK", "keywords": ["mobile", "sdk", "integration", "app", "trigger", "cloud", "setup", "configuration"]},
        {"title": "Email Campaign Creation", "url": "https://help.blueshift.com/hc/en-us/articles/115002714173-Email-campaigns", "keywords": ["email", "campaign", "create", "setup", "subject", "line", "personalization", "template"]},
        {"title": "Personalization and Dynamic Content", "url": "https://help.blueshift.com/hc/en-us/articles/115002714253-Personalization", "keywords": ["personalization", "dynamic", "content", "subject", "line", "custom", "attributes", "merge"]},
        {"title": "Segmentation Overview", "url": "https://help.blueshift.com/hc/en-us/articles/115002669413-Segmentation-overview", "keywords": ["segmentation", "audience", "targeting", "segments", "customer", "groups", "filters"]},
        {"title": "Facebook Conversions API", "url": "https://help.blueshift.com/hc/en-us/articles/24009984649235-Facebook-Conversions-API", "keywords": ["facebook", "conversions", "api", "integration", "tracking", "audience", "syndication", "setup"]},
        {"title": "Facebook Audience Syndication", "url": "https://help.blueshift.com/hc/en-us/articles/360046864473-Facebook-audience", "keywords": ["facebook", "audience", "syndication", "lookalike", "custom", "integration", "setup", "troubleshoot"]},
        {"title": "External Fetch Configuration", "url": "https://help.blueshift.com/hc/en-us/articles/360006449754-External-fetch", "keywords": ["external", "fetch", "api", "integration", "troubleshoot", "error", "failed", "configuration"]},
        {"title": "Webhook Integration Setup", "url": "https://help.blueshift.com/hc/en-us/articles/115002714333-Webhooks", "keywords": ["webhook", "integration", "api", "external", "setup", "troubleshoot", "failed", "configuration"]},
    ]

    query_lower = query.lower()
    query_words = set(query_lower.split())

    stop_words = {'the', 'a', 'an', 'and', 'or', 'but'}
    clean_query_words = [w for w in query_words if w not in stop_words and len(w) > 1]

    scored_docs = []
    for doc in help_docs_expanded:
        score = 0
        title_words = set(doc['title'].lower().split())
        title_matches = len([w for w in clean_query_words if w in title_words])
        score += title_matches * 8

        keyword_words = set(' '.join(doc['keywords']).lower().split())
        keyword_matches = len([w for w in clean_query_words if w in keyword_words])
        score += keyword_matches * 4

        if 'trigger' in clean_query_words:
            if 'trigger' in doc['keywords']:
                score += 15 
            if any(word in clean_query_words for word in ['app', 'mobile', 'cloud']):
                if any(word in doc['keywords'] for word in ['mobile', 'app', 'push', 'cloud']):
                    score += 10

        if any(word in clean_query_words for word in ['not', 'troubleshoot', 'debug', 'help', 'issue']):
            if any(word in doc['keywords'] for word in ['troubleshoot', 'not', 'working', 'debug']):
                score += 8

        if score > 0:
            scored_docs.append((score, doc))

    scored_docs.sort(reverse=True, key=lambda x: x[0])
    results = [doc for score, doc in scored_docs[:limit]]

    logger.info(f"Help docs search (fallback): '{query}' -> found {len(results)} results")
    return results

def search_blueshift_api_docs(query, limit=3):
    """Search Blueshift API documentation (kept same)"""
    try:
        # Search the main API reference page with working endpoint URLs
        api_docs = [
            {"title": "Blueshift API Documentation - Overview", "url": "https://developer.blueshift.com/reference/welcome", "keywords": ["api", "developer", "documentation", "reference", "guide", "overview"]},
            {"title": "Events API - POST /api/v1/event", "url": "https://developer.blueshift.com/reference/post_api-v1-event", "keywords": ["events", "api", "custom", "attribute", "user", "tracking", "data", "event"]},
            {"title": "Customer API - POST /api/v1/customers", "url": "https://developer.blueshift.com/reference/post_api-v1-customers", "keywords": ["customer", "user", "profile", "custom", "attribute", "identify", "customers"]},
            {"title": "Customer Search API - GET /api/v1/customers", "url": "https://developer.blueshift.com/reference/get_api-v1-customers", "keywords": ["customer", "search", "user", "profile", "lookup"]},
            {"title": "Campaigns API - GET /api/v1/campaigns", "url": "https://developer.blueshift.com/reference/get_api-v1-campaigns", "keywords": ["campaigns", "api", "messaging", "email", "push", "sms"]},
            {"title": "Catalog API - POST /api/v1/catalog", "url": "https://developer.blueshift.com/reference/post_api-v1-catalog", "keywords": ["catalog", "products", "recommendations", "data"]},
        ]

        query_lower = query.lower()
        query_words = set(query_lower.split())

        # Score based on keyword matching
        scored_docs = []
        for doc in api_docs:
            score = 0

            # Title matching
            title_words = set(doc['title'].lower().split())
            title_matches = query_words.intersection(title_words)
            score += len(title_matches) * 5

            # Keyword matching
            keyword_words = set(' '.join(doc['keywords']).lower().split())
            keyword_matches = query_words.intersection(keyword_words)
            score += len(keyword_matches) * 3

            # Special scoring for specific terms
            if any(word in query_lower for word in ['custom', 'attribute']):
                if 'attribute' in doc['keywords']:
                    score += 10

            if 'api' in query_lower:
                if 'api' in doc['keywords']:
                    score += 5

            if score > 0:
                scored_docs.append((score, doc))

        # Sort by score and return top results
        scored_docs.sort(reverse=True, key=lambda x: x[0])
        results = [{"title": doc['title'], "url": doc['url']} for score, doc in scored_docs[:limit]]

        logger.info(f"Blueshift API docs search: '{query}' -> found {len(results)} results")
        return results

    except Exception as e:
        logger.error(f"Blueshift API docs search error: {e}")
        return []

# --- FIX 4: Robust fetch_help_doc_content with BeautifulSoup Fallback (Kept same) ---
def fetch_help_doc_content_improved(url, max_content_length=2000):
    """Improved content fetching with fallback for missing BeautifulSoup"""
    try:
        logger.info(f"Fetching content from: {url}")

        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        }

        response = requests.get(url, timeout=15, headers=headers)
        if response.status_code != 200:
            logger.warning(f"Failed to fetch {url}: Status {response.status_code}")
            return ""

        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Remove unwanted elements
            for tag in soup(['script', 'style', 'nav', 'header', 'footer', 'aside', 'form']):
                tag.decompose()

            # Find main content using common selectors
            main_content = soup.select_one('article .article-body') or soup.select_one('.article-content') or soup.find('article') or soup.body or soup
            
            # Extract text with structure
            text_content = ""
            for element in main_content.find_all(['h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'p', 'li']):
                text = element.get_text(strip=True)
                if text and len(text) > 15:
                    if element.name.startswith('h'):
                        text_content += f"\n[HEADING] {text}\n"
                    else:
                        text_content += f"{text}\n"
            
            clean_content = '\n'.join([line.strip() for line in text_content.split('\n') if line.strip()])
            
            if len(clean_content) > max_content_length:
                clean_content = clean_content[:max_content_length] + "...[truncated]"
            
            logger.info(f"Successfully extracted {len(clean_content)} characters using BeautifulSoup")
            return clean_content
            
        except ImportError:
            # Fallback: simple text extraction without BeautifulSoup
            logger.warning("BeautifulSoup not available - using simple text extraction")
            
            # Basic HTML stripping (not perfect but functional)
            text = response.text
            
            # Remove scripts and styles
            text = re.sub(r'<script[^>]*>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            
            # Remove HTML tags
            text = re.sub(r'<[^>]+>', ' ', text)
            
            # Clean up whitespace
            text = re.sub(r'\s+', ' ', text).strip()
            
            if len(text) > max_content_length:
                text = text[:max_content_length] + "...[truncated]"
            
            logger.info(f"Fallback extraction: {len(text)} characters")
            return text

    except Exception as e:
        logger.error(f"Error fetching content from {url}: {e}")
        return ""
# --- END FIX 4 ---

# --- CRITICAL FIX: TRULY LENIENT Validation Function ---
def validate_search_results_improved(query, results, source_name):
    """TRULY LENIENT validation - Accept most results unless entirely irrelevant."""
    if not results:
        return []

    query_lower = query.lower()
    query_words = set(query.lower().split()) # FIX: ensure the query words are lowercased here
    validated_results = []

    # Remove only the most basic stop words - keep more meaningful words
    stop_words = {'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'could', 'should'}
    clean_query_words = [w for w in query_words if w not in stop_words and len(w) > 2]

    for result in results:
        title = result.get('title', '').lower()
        url = result.get('url', '')
        # Also check description/summary if available
        description = result.get('description', '').lower()
        summary = result.get('summary', '').lower()
        content = f"{title} {description} {summary}".lower()

        # Set default to ACCEPT (the core fix)
        should_include = True

        # Check for ANY relevance in title OR content
        meaningful_matches = len([w for w in clean_query_words if w in content])

        # Special handling for Confluence - be extremely lenient since Confluence search already did relevance filtering
        if source_name == "Confluence":
            should_include = True  # Accept all Confluence results since they passed Confluence's own relevance filtering
        elif source_name == "JIRA":
            # Be very lenient with JIRA results since they're already scored and filtered
            should_include = True  # Accept JIRA results that made it through the scoring system
        elif meaningful_matches == 0:
            # Expanded platform terms for better context matching
            blueshift_terms = {'campaign', 'trigger', 'api', 'event', 'customer', 'journey', 'studio', 'message', 'mobile', 'app', 'push', 'zendesk', 'jira', 'confluence', 'facebook', 'audience', 'lookalike', 'syndication', 'integration', 'external', 'fetch', 'optimizer', 'email', 'sms', 'segment', 'webhook', 'personalization', 'recommendation', 'error', 'failed', 'limit', 'channel', 'delivery', 'bounce'}
            has_blueshift = any(term in content for term in blueshift_terms)

            if not has_blueshift:
                should_include = False  # Only reject if truly irrelevant

        if should_include and url:  # Must have valid URL
            validated_results.append(result)
            logger.info(f"✅ {source_name} - Included: {result.get('title', 'Untitled')[:60]}...")
        else:
            logger.info(f"❌ {source_name} - Excluded: {result.get('title', 'Untitled')[:60]}...")
            
    logger.info(f"{source_name} validation: {len(results)} → {len(validated_results)} results")
    return validated_results
# --- END CRITICAL FIX ---

def verify_step_extraction(query, resources_with_content):
    """Verify if actual step-by-step instructions exist in the content (kept for completeness)"""
    # This function is not used in the current flow, but kept in case it is reintroduced.
    pass

# --- FIX 5: Update Main Resource Generation Function (Kept same, calls updated searches) ---
def generate_related_resources_improved(query):
    """Improved resource generation with better validation and search calls"""
    logger.info(f"🔍 Searching for resources: '{query}'")

    # Perform searches with restored/improved functions
    help_docs = validate_search_results_improved(query, search_help_docs(query, limit=4), "Help Docs")
    
    # We apply validation manually on the Confluence raw results for troubleshooting,
    # then include them all if they were found (to debug the Confluence validation step)
    confluence_raw = search_confluence_docs_improved(query, limit=4)
    # FIX: Confluence validation is now run, but because the base filter is so strict, 
    # we rely on it now being correctly filtered.
    confluence_docs = validate_search_results_improved(query, confluence_raw, "Confluence")

    jira_tickets = validate_search_results_improved(query, search_jira_tickets_improved(query, limit=4), "JIRA")
    support_tickets = validate_search_results_improved(query, search_zendesk_tickets_improved(query, limit=4), "Zendesk")
    api_docs = validate_search_results_improved(query, search_blueshift_api_docs(query, limit=3), "API Docs")

    logger.info(f"📊 Final counts: Help={len(help_docs)}, Confluence={len(confluence_docs)}, JIRA={len(jira_tickets)}, Zendesk={len(support_tickets)}, API={len(api_docs)}")

    # Fetch content from top results
    resources_with_content = []

    # Prioritize help docs and API docs for content fetching
    priority_resources = help_docs[:2] + api_docs[:2] + confluence_docs[:1] # Include 1 top confluence doc

    # Use the improved content fetching function
    for doc in priority_resources:
        if doc.get('url'):
            content = fetch_help_doc_content_improved(doc['url'])
            if content and len(content.strip()) > 50:  # Must have meaningful content
                resources_with_content.append({
                    'title': doc['title'],
                    'url': doc['url'],
                    'content': content,
                    'source': 'help_docs' if doc in help_docs else ('confluence' if doc in confluence_docs else 'api_docs')
                })
                logger.info(f"✅ Fetched content: {doc['title'][:60]}... ({len(content)} chars)")

    # Add JIRA and Zendesk details without full fetch
    for ticket in jira_tickets[:2]:
        if ticket.get('url'):
            ticket_content = f"JIRA Ticket: {ticket['title']}\nThis engineering ticket may contain platform navigation steps or UI element references."
            resources_with_content.append({
                'title': ticket['title'],
                'url': ticket['url'],
                'content': ticket_content,
                'source': 'jira'
            })

    for ticket in support_tickets[:2]:
        if ticket.get('url'):
            ticket_content = f"Support Ticket: {ticket['title']}\nThis support ticket may contain step-by-step platform navigation instructions provided by agents."
            resources_with_content.append({
                'title': ticket['title'],
                'url': ticket['url'],
                'content': ticket_content,
                'source': 'zendesk'
            })

    logger.info(f"📄 Resources with content: {len(resources_with_content)}")

    return {
        'help_docs': help_docs,
        'confluence_docs': confluence_docs,
        'jira_tickets': jira_tickets,
        'support_tickets': support_tickets,
        'api_docs': api_docs,
        'platform_resources_with_content': resources_with_content
    }
# --- END FIX 5 ---

def get_athena_client():
    """Initialize AWS Athena client"""
    try:
        # Use boto3 to create Athena client - will use AWS credentials from environment or instance profile
        return boto3.client('athena', region_name=AWS_REGION)
    except Exception as e:
        print(f"Error initializing Athena client: {e}")
        return None

def query_athena(query_string, database_name, query_description="Athena query"):
    """Execute a query on AWS Athena and return results"""
    # NOTE: This function is not used for the AI workflow, only for manual user data lookup.
    try:
        athena_client = get_athena_client()
        if not athena_client:
            return {"error": "Could not initialize Athena client", "data": []}

        # Start query execution
        response = athena_client.start_query_execution(
            QueryString=query_string,
            QueryExecutionContext={'Database': database_name},
            ResultConfiguration={'OutputLocation': ATHENA_S3_OUTPUT}
        )

        query_execution_id = response['QueryExecutionId']

        # Wait for query to complete
        max_attempts = 30  # Wait up to 30 seconds
        for attempt in range(max_attempts):
            result = athena_client.get_query_execution(QueryExecutionId=query_execution_id)
            status = result['QueryExecution']['Status']['State']

            if status in ['SUCCEEDED', 'FAILED', 'CANCELLED']:
                break
            time.sleep(1)

        if status != 'SUCCEEDED':
            status_details = result['QueryExecution']['Status']
            error_msg = status_details.get('StateChangeReason', 'Query failed')
            failure_reason = status_details.get('AthenaError', {}).get('ErrorMessage', 'No additional error details')

            print(f"Athena query failed:")
            print(f"  Status: {status}")
            print(f"  StateChangeReason: {error_msg}")
            print(f"  AthenaError: {failure_reason}")
            print(f"  Full status: {status_details}")

            # Note: The output showed Access Denied here. The fix is external (AWS permissions),
            # but we ensure the error message is clear.
            return {"error": f"Query failed: {error_msg}. Details: {failure_reason}", "data": []}

        # Get query results
        results = athena_client.get_query_results(QueryExecutionId=query_execution_id)

        # Parse results
        rows = results['ResultSet']['Rows']
        if not rows:
            return {"data": [], "columns": []}

        # Extract column headers
        columns = [col['VarCharValue'] for col in rows[0]['Data']]

        # Extract data rows
        data = []
        for row in rows[1:]:  # Skip header row
            row_data = {}
            for i, col in enumerate(row['Data']):
                row_data[columns[i]] = col.get('VarCharValue', '')
            data.append(row_data)

        return {"data": data, "columns": columns}

    except Exception as e:
        print(f"Athena query error: {e}")
        print(f"Query was: {query_string}")
        return {"error": str(e), "data": []}

def customize_query_for_execution(sql_query, user_query):
    """Keep the query as a template with placeholder values - do not substitute real data"""

    # Keep placeholder values for account UUIDs and other sensitive data
    # Replace any real UUIDs that might have been inserted with placeholders
    import re

    # Replace any real UUID patterns with placeholder text
    uuid_pattern = r'[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}'
    customized = re.sub(uuid_pattern, 'client_account_uuid', sql_query)

    # Ensure placeholder values are used for common fields
    customized = customized.replace('11d490bf-b250-4749-abf4-b6197620a985', 'client_account_uuid')

    # Use example placeholder dates instead of real dates - fix pattern matching
    import re
    # Replace file_date patterns with fixed example date
    customized = re.sub(r"file_date >= '[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}'", "file_date >= '2024-12-01'", customized)
    customized = re.sub(r"file_date < '[0-9]{4}-[0-9]{1,2}-[0-9]{1,2}'", "file_date < '2024-12-15'", customized)

    # Ensure other sensitive fields use placeholders
    if 'user_uuid' in customized and 'client_user_uuid' not in customized:
        customized = re.sub(r"user_uuid = '[^']*'", "user_uuid = 'client_user_uuid'", customized)
    if 'campaign_uuid' in customized and 'client_campaign_uuid' not in customized:
        customized = re.sub(r"campaign_uuid = '[^']*'", "campaign_uuid = 'client_campaign_uuid'", customized)
    if 'trigger_uuid' in customized and 'client_trigger_uuid' not in customized:
        customized = re.sub(r"trigger_uuid = '[^']*'", "trigger_uuid = 'client_trigger_uuid'", customized)

    return customized

def get_available_tables(database_name):
    """Get list of available tables in the database - returns hardcoded list to avoid S3 permission issues"""
    try:
        # Return a hardcoded list of known tables to avoid executing queries that require S3 permissions
        if database_name == 'customer_campaign_logs':
            return ['campaign_execution_v3', 'user_events', 'campaign_metrics', 'email_events', 'sms_events']
        else:
            return ['campaign_execution_v3']  # Default fallback
    except Exception as e:
        print(f"Error getting tables: {e}")
        return []

# Cache for common message patterns - instant lookup, no database query needed
MESSAGE_PATTERN_CACHE = {
    # Quiet hours
    'quiet': 'QuietHours',
    'hours': 'QuietHours',

    # Facebook/Social
    'facebook': 'FacebookAudienceSync',
    'fb': 'FacebookAudienceSync',
    'sync': 'FacebookAudienceSync',
    'audience': 'FacebookAudienceSync',
    'lookalike': 'FacebookAudienceSync',
    'syndication': 'FacebookAudienceSync',

    # Errors
    'external': 'ExternalFetchError',
    'fetch': 'ExternalFetchError',
    'channel': 'ChannelLimitError',
    'limit': 'ChannelLimitError',
    'dedup': 'DeduplicationError',
    'dedupe': 'DeduplicationError',
    'deduplication': 'DeduplicationError',
    'duplicate': 'DeduplicationError',
    'bounce': 'SoftBounce',
    'bounced': 'SoftBounce',

    # Triggers & Journey
    'trigger': 'TriggerEvaluation',
    'triggered': 'TriggerEvaluation',
    'journey': 'UserJourney',
    'evaluation': 'TriggerEvaluation',

    # Suppression & Opt-out
    'suppression': 'SuppressionCheck',
    'suppressed': 'SuppressionCheck',
    'optout': 'OptOutCheck',
    'unsubscribe': 'UnsubscribeCheck',

    # Channels
    'sms': 'SMSDelivery',
    'email': 'EmailDelivery',
    'push': 'PushNotification',
    'mobile': 'PushNotification',
    'webhook': 'WebhookExecution',

    # Other common issues
    'timeout': 'TimeoutError',
    'rate': 'RateLimitError',
    'throttle': 'RateLimitError',
    'api': 'APIError',
    'permission': 'PermissionError',
    'authentication': 'AuthenticationError',
    'auth': 'AuthenticationError'
}

def sample_message_patterns(user_query, database_name, timeout_seconds=5):
    """Sample the database to find actual message patterns related to the user's query.

    Uses caching for common patterns and has a fast timeout to avoid delays.
    """
    try:
        # Extract key terms from user query
        STOP_WORDS = {'why', 'is', 'my', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'how', 'what', 'when', 'where', 'who'}
        words = [w for w in user_query.lower().split() if len(w) > 2 and w.lower() not in STOP_WORDS]

        if not words:
            return None

        # Check cache first - instant lookup
        search_term = words[0].lower()
        if search_term in MESSAGE_PATTERN_CACHE:
            cached_pattern = MESSAGE_PATTERN_CACHE[search_term]
            logger.info(f"Using cached pattern for '{search_term}': {cached_pattern}")
            return cached_pattern

        # Check if any word matches cache
        for word in words:
            if word.lower() in MESSAGE_PATTERN_CACHE:
                cached_pattern = MESSAGE_PATTERN_CACHE[word.lower()]
                logger.info(f"Using cached pattern for '{word}': {cached_pattern}")
                return cached_pattern

        logger.info(f"No cached pattern found, sampling database for: {words}")

        # OPTIMIZED: Use recent partition and limit for speed
        sample_query = f"""
        select message
        from {database_name}.campaign_execution_v3
        where lower(message) like '%{search_term}%'
        and file_date >= date_add('day', -7, current_date)
        limit 3
        """

        # Add timeout wrapper using threading
        import threading
        result_container = [None]
        error_container = [None]

        def run_query():
            try:
                result_container[0] = query_athena(sample_query, database_name, f"Sample messages for {search_term}")
            except Exception as e:
                error_container[0] = e

        query_thread = threading.Thread(target=run_query)
        query_thread.daemon = True
        query_thread.start()
        query_thread.join(timeout=timeout_seconds)

        if query_thread.is_alive():
            logger.warning(f"Database sampling timed out after {timeout_seconds}s, using AI inference")
            return None

        if error_container[0]:
            logger.error(f"Database sampling error: {error_container[0]}")
            return None

        result = result_container[0]

        if result and result.get('data') and len(result['data']) > 0:
            # Extract patterns from actual messages
            messages = [row.get('message', '') for row in result['data']]
            logger.info(f"Found {len(messages)} sample messages containing '{search_term}'")

            # Return the most common distinct patterns (simplified - just return first match)
            if messages:
                # Look for key terms in the actual messages
                for msg in messages:
                    if search_term.lower() in msg.lower():
                        # Find the actual casing used in the message
                        import re
                        pattern = re.search(rf'\b\w*{search_term}\w*\b', msg, re.IGNORECASE)
                        if pattern:
                            actual_term = pattern.group(0)
                            logger.info(f"Found actual message pattern: {actual_term}")
                            # Cache the result for future queries
                            MESSAGE_PATTERN_CACHE[search_term] = actual_term
                            return actual_term

        logger.info(f"No message patterns found for '{search_term}', AI will guess")
        return None

    except Exception as e:
        logger.error(f"Error sampling message patterns: {e}")
        return None

def generate_athena_insights(user_query):
    """Generate data insights using Athena queries based on user query with improved relevance"""
    try:
        # Same stop words filtering as other searches
        STOP_WORDS = {'why', 'is', 'my', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'how', 'what', 'when', 'where', 'who'}

        def clean_words(words):
            return [w for w in words if len(w) > 2 and w.lower() not in STOP_WORDS]

        # Extract key terms from user query
        words = user_query.strip().split()
        clean_query_words = clean_words(words)
        if not clean_query_words:
            clean_query_words = words

        logger.info(f"Athena query generation - Original: '{user_query}' -> Key terms: {clean_query_words}")

        # Get available tables first
        database_name = ATHENA_DATABASES[0]  # Use first database
        available_tables = get_available_tables(database_name)
        table_list = ', '.join(available_tables[:20]) if available_tables else "customer_campaign_logs.campaign_execution_v3"

        # Extract UUIDs from user query if provided
        import re
        uuid_pattern = r'[a-fA-F0-9]{8}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{4}-[a-fA-F0-9]{12}'
        found_uuids = re.findall(uuid_pattern, user_query)

        uuid_context = ""
        if found_uuids:
            uuid_context = f"\n\nUUIDs FOUND IN USER QUERY:\n"
            for uuid in found_uuids:
                uuid_context += f"- {uuid}\n"
            uuid_context += "Include these specific UUIDs in the query (use as user_uuid, campaign_uuid, or account_uuid based on context).\n"
            logger.info(f"Found UUIDs in query: {found_uuids}")

        # Sample the database to find actual message patterns (DISABLED for performance - use cache only)
        # actual_pattern = sample_message_patterns(user_query, database_name)

        # Use cache-only pattern matching for instant response
        actual_pattern = None
        STOP_WORDS = {'why', 'is', 'my', 'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'how', 'what', 'when', 'where', 'who'}
        words = [w for w in user_query.lower().split() if len(w) > 2 and w.lower() not in STOP_WORDS]

        # Check cache for instant pattern matching
        for word in words:
            if word in MESSAGE_PATTERN_CACHE:
                actual_pattern = MESSAGE_PATTERN_CACHE[word]
                logger.info(f"Using cached pattern for '{word}': {actual_pattern}")
                break

        pattern_context = ""
        if actual_pattern:
            pattern_context = f"\n\nKNOWN MESSAGE PATTERN:\nUse '{actual_pattern}' in your message like condition (verified pattern from common support queries).\n"
        else:
            logger.info("No cached pattern found, AI will infer from query")

        # --- Use the new call_gemini_api for analysis ---
        # FIX: Explicitly enforce the full table name in the template examples.
        analysis_prompt = f"""You are a Blueshift data analyst. Generate a relevant Athena SQL query for this support question: "{user_query}"{uuid_context}{pattern_context}

AVAILABLE DATA:
- Database: {database_name}
- Main table: customer_campaign_logs.campaign_execution_v3
- Key columns: timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level, worker_name, transaction_uuid, execution_key

ANALYZE THE USER'S QUESTION and generate a query that matches actual Blueshift support query patterns.

IMPORTANT: Generate DETAILED, CONTEXTUAL queries based on the question type.

QUERY PATTERNS BY SCENARIO:

1. USER JOURNEY / "Why didn't user get message?" / Feature troubleshooting:
select timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level, worker_name
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and user_uuid = 'client_user_uuid'
and campaign_uuid = 'client_campaign_uuid'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
order by timestamp asc
limit 500

WHY THIS QUERY: Shows complete user journey through campaign - all evaluations, checks, errors, successes in chronological order.
INCLUDES: trigger_uuid (which trigger fired), log_level (errors vs info), worker_name (which server processed)

2. ERROR INVESTIGATION / "Why are messages failing?":
select timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level, execution_key
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and campaign_uuid = 'client_campaign_uuid'
and log_level = 'ERROR'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
order by timestamp desc
limit 200

WHY THIS QUERY: Focuses on errors only, recent first. execution_key links related log entries.

3. FEATURE SPECIFIC / "Quiet hours not working" / "Facebook sync issues":
select timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and campaign_uuid = 'client_campaign_uuid'
and message like '%{feature_pattern}%'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
order by timestamp asc
limit 300

WHY THIS QUERY: Filters for specific feature logs, shows chronological progression of feature behavior.

4. VOLUME ANALYSIS / "How many users affected?":
select
  file_date,
  count(distinct user_uuid) as affected_users,
  count(*) as total_occurrences,
  log_level
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and campaign_uuid = 'client_campaign_uuid'
and message like '%{error_pattern}%'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
group by file_date, log_level
order by file_date desc

WHY THIS QUERY: Shows impact over time - how many users hit the issue per day.

5. CAMPAIGN PERFORMANCE / "Is campaign sending?":
select
  log_level,
  count(*) as count,
  count(distinct user_uuid) as unique_users
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and campaign_uuid = 'client_campaign_uuid'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
group by log_level
order by count desc

WHY THIS QUERY: Summary view - how many ERRORs vs INFOs, overall campaign health.

ANALYZE THE USER'S QUESTION AND CHOOSE THE RIGHT PATTERN:
- Journey questions → Pattern 1 (detailed user journey)
- Error questions → Pattern 2 (error-focused)
- Feature questions → Pattern 3 (feature-specific)
- "How many" questions → Pattern 4 (volume analysis)
- "Is it working" questions → Pattern 5 (summary stats)

CRITICAL QUERY RULES:
1. **Include relevant columns** based on query type:
   - User journey: timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level, worker_name
   - Error investigation: timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level, execution_key
   - Volume analysis: Use COUNT, GROUP BY, aggregations
   - Feature-specific: timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level

2. **Use appropriate WHERE conditions**:
   - Always: account_uuid, file_date range
   - Journey queries: Add user_uuid, campaign_uuid
   - Error queries: Add log_level = 'ERROR'
   - Feature queries: Add message like '%pattern%' (ONLY ONE)

3. **Set appropriate LIMIT**:
   - User journey: 500 (need full story)
   - Error investigation: 200 (enough to see patterns)
   - Feature-specific: 300 (see progression)
   - Volume analysis: No limit needed (aggregated)

4. **Use correct ORDER BY**:
   - Journey queries: timestamp ASC (chronological story)
   - Error queries: timestamp DESC (recent errors first)
   - Volume queries: date DESC or count DESC

5. **Date ranges**:
   - Use: file_date >= '2024-12-01' AND file_date < '2024-12-15'
   - This ensures partition pruning for performance

EXAMPLE OUTPUTS:

For "Why didn't user 123 receive message?":
```sql
select timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level, worker_name
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and user_uuid = 'client_user_uuid'
and campaign_uuid = 'client_campaign_uuid'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
order by timestamp asc
limit 500
```

For "How many users are getting channel limit errors?":
```sql
select
  file_date,
  count(distinct user_uuid) as affected_users,
  count(*) as total_occurrences
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and campaign_uuid = 'client_campaign_uuid'
and message like '%ChannelLimitError%'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
group by file_date
order by file_date desc
```

For "Are there Facebook sync errors?":
```sql
select timestamp, user_uuid, campaign_uuid, trigger_uuid, message, log_level
from customer_campaign_logs.campaign_execution_v3
where account_uuid = 'client_account_uuid'
and campaign_uuid = 'client_campaign_uuid'
and log_level = 'ERROR'
and message like '%FacebookAudienceSync%'
and file_date >= '2024-12-01'
and file_date < '2024-12-15'
order by timestamp desc
limit 200
```

Generate a query specifically for: "{user_query}"

Format your response as:
DATABASE: {database_name}

SQL_QUERY:
[Write the exact format shown above - each clause on its own line with proper spacing]

INSIGHT_EXPLANATION:
[Brief explanation of what this query searches for and why it helps with the user's question]"""

        # Call the unified Gemini API function (temperature 0.0 for deterministic SQL generation)
        ai_response = call_gemini_api(query=analysis_prompt, platform_resources=None, temperature=0.0)
        # --- End Gemini API call ---

        if ai_response.startswith("Error:"):
            logger.error(f"Athena AI API error: {ai_response}")
            return get_default_athena_insights(user_query)

        logger.info(f"Athena AI response: {ai_response[:200]}...")
        return parse_athena_analysis(ai_response, user_query)

    except Exception as e:
        logger.error(f"Athena insights generation error: {e}")
        return get_default_athena_insights(user_query)

def parse_athena_analysis(ai_response, user_query):
    """Parse AI response and execute Athena query"""
    try:
        lines = ai_response.split('\n')
        database_name = ATHENA_DATABASES[0]  # Default to first database
        sql_query = ""
        explanation = ""

        in_database_section = False
        in_sql_section = False
        in_explanation_section = False

        for line in lines:
            line = line.strip()
            if line.startswith('DATABASE:'):
                in_database_section = True
                in_sql_section = False
                in_explanation_section = False
                continue
            elif line.startswith('SQL_QUERY:'):
                in_database_section = False
                in_sql_section = True
                in_explanation_section = False
                continue
            elif line.startswith('INSIGHT_EXPLANATION:'):
                in_database_section = False
                in_sql_section = False
                in_explanation_section = True
                continue

            if in_database_section and line:
                # Check if the suggested database is in our list
                if line in ATHENA_DATABASES:
                    database_name = line
            elif in_sql_section and line:
                # Clean up markdown formatting
                cleaned_line = line.replace('```sql', '').replace('```', '').strip()
                if cleaned_line:  # Only add non-empty lines
                    sql_query += cleaned_line + "\n"
            elif in_explanation_section and line:
                explanation += line + "\n"

        # Validate and refine the query before returning
        if sql_query.strip():
            # Ensure we use placeholder values instead of real data
            safe_sql_query = customize_query_for_execution(sql_query.strip(), user_query)

            # TEST QUERY: Validate it works and return sample results
            validated_query = validate_and_test_query(safe_sql_query, database_name, user_query, explanation.strip())

            if validated_query:
                return validated_query
            else:
                # If validation fails, return original query
                print(f"Generated SQL Query: {safe_sql_query}")  # Debug output
                return {
                    'database': database_name,
                    'sql_query': safe_sql_query,
                    'explanation': explanation.strip() + "\n\n💡 Copy this query to AWS Athena console and customize with specific account_uuid, campaign_uuid, and date ranges for your support case.",
                    'results': {"note": "Query template ready for manual customization in Athena", "data": []},
                    'has_data': False
                }
        else:
            return get_default_athena_insights(user_query)

    except Exception as e:
        print(f"Error parsing Athena analysis: {e}")
        return get_default_athena_insights(user_query)

def validate_and_test_query(sql_query, database_name, user_query, explanation):
    """Validate the query by testing it with a small sample and return refined version with actual results"""
    try:
        logger.info("Validating and testing generated Athena query...")

        # Create a test version of the query - remove placeholders and add realistic test conditions
        test_query = sql_query.replace("'client_account_uuid'", "(select account_uuid from customer_campaign_logs.campaign_execution_v3 limit 1)")
        test_query = test_query.replace("'client_campaign_uuid'", "(select campaign_uuid from customer_campaign_logs.campaign_execution_v3 where campaign_uuid is not null limit 1)")
        test_query = test_query.replace("'client_user_uuid'", "(select user_uuid from customer_campaign_logs.campaign_execution_v3 where user_uuid is not null limit 1)")

        # Ensure it's limited to prevent long queries
        if 'limit' not in test_query.lower():
            test_query += "\nlimit 10"
        else:
            # Replace any large limits with 10 for testing
            import re
            test_query = re.sub(r'limit\s+\d+', 'limit 10', test_query, flags=re.IGNORECASE)

        logger.info(f"Testing query: {test_query[:200]}...")

        # Execute the test query with short timeout
        result = query_athena(test_query, database_name, f"Validate query for: {user_query}")

        if result and not result.get('error'):
            # Query works! Get the actual structure
            sample_data = result.get('data', [])
            columns = result.get('columns', [])

            logger.info(f"✅ Query validated successfully! Found {len(sample_data)} sample rows")

            # Build enhanced explanation with sample results
            enhanced_explanation = explanation + "\n\n"

            if sample_data and len(sample_data) > 0:
                enhanced_explanation += f"✅ **Query Validated**: This query successfully returns results from your database.\n\n"
                enhanced_explanation += f"**Sample Results Preview** ({len(sample_data)} rows):\n"
                for i, row in enumerate(sample_data[:3], 1):
                    enhanced_explanation += f"\n{i}. "
                    # Show first few columns
                    for col in columns[:3]:
                        value = row.get(col, 'N/A')
                        enhanced_explanation += f"{col}: {str(value)[:50]}... | "
                    enhanced_explanation = enhanced_explanation.rstrip(' | ')
            else:
                enhanced_explanation += "⚠️ **Query is valid but returned no results**. This might mean:\n"
                enhanced_explanation += "- The message pattern doesn't exist in recent logs\n"
                enhanced_explanation += "- The date range needs adjustment\n"
                enhanced_explanation += "- Try a broader search term\n"

            enhanced_explanation += "\n\n💡 **Next Steps**: Copy this query and replace placeholders with your specific account_uuid, campaign_uuid, user_uuid, and date range."

            return {
                'database': database_name,
                'sql_query': sql_query,  # Return original with placeholders
                'explanation': enhanced_explanation,
                'results': {"data": sample_data[:5], "columns": columns, "note": "Sample results from validation query"},
                'has_data': True if sample_data else False
            }
        else:
            # Query failed
            error_msg = result.get('error', 'Unknown error')
            logger.warning(f"❌ Query validation failed: {error_msg}")

            # Return query with error context
            return {
                'database': database_name,
                'sql_query': sql_query,
                'explanation': explanation + f"\n\n⚠️ **Validation Note**: Initial test of this query encountered an issue ({error_msg}). You may need to adjust the query parameters for your specific use case.",
                'results': {"note": "Query validation failed - may need adjustments", "data": []},
                'has_data': False
            }

    except Exception as e:
        logger.error(f"Error validating query: {e}")
        return None  # Fall back to original behavior

def get_default_athena_insights(user_query):
    """Provide default Athena insights when AI analysis fails"""
    # Use the simplest possible working query
    database_name = ATHENA_DATABASES[0]
    default_query = f"""SELECT timestamp, message
FROM {database_name}.campaign_execution_v3
WHERE log_level = 'ERROR'
ORDER BY timestamp DESC
LIMIT 10"""

    return {
        'database': database_name,
        'sql_query': default_query,
        'explanation': f'Recent error logs from campaign_execution_v3 related to: {user_query}',
        'results': {"data": [], "columns": [], "note": "Sample query - will show real data when executed"},
        'has_data': False
    }

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        data = request.get_json()
        username = data.get('username', '')
        password = data.get('password', '')

        # Check credentials - Admin login
        if username == 'Admin' and password == 'BlueShiftAdmin#2025!':
            session['logged_in'] = True
            session['is_admin'] = True
            session['agent_identified'] = False  # Flag to show identification prompt
            session.permanent = True
            return jsonify({'success': True})
        # Regular support agent login
        elif username == 'Blueshift Support' and password == 'BlueS&n@*9072!':
            session['logged_in'] = True
            session['is_admin'] = False
            session['agent_identified'] = False  # Flag to show identification prompt
            session.permanent = True
            return jsonify({'success': True})
        else:
            return jsonify({'success': False, 'error': 'Invalid username or password'})

    return render_template_string(LOGIN_TEMPLATE)

@app.route('/identify-agent', methods=['POST'])
def identify_agent():
    """Store individual agent name after shared login"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'error': 'Not logged in'}), 401

    data = request.get_json()
    agent_name = data.get('agent_name', '').strip()

    if not agent_name:
        return jsonify({'success': False, 'error': 'Please enter your name'})

    session['agent_name'] = agent_name
    session['agent_identified'] = True

    return jsonify({'success': True})

@app.route('/')
def index():
    # Check if user is logged in
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    is_admin = session.get('is_admin', False)
    return render_template_string(MAIN_TEMPLATE, is_admin=is_admin)

@app.route('/check-admin')
def check_admin():
    """Check if current user is admin"""
    if not session.get('logged_in'):
        return jsonify({'is_admin': False})
    return jsonify({'is_admin': session.get('is_admin', False)})

@app.route('/blueshift-favicon.png')
def favicon():
    """Serve the Blueshift favicon"""
    try:
        # Use send_file to correctly serve the file
        return send_file('blueshift-favicon.png', mimetype='image/png')
    except Exception as e:
        logger.error(f"Error serving favicon: {e}")
        return '', 404

@app.route('/favicon.ico')
def favicon_ico():
    """Serve favicon.ico (redirect to PNG)"""
    try:
        return send_file('blueshift-favicon.png', mimetype='image/png')
    except Exception as e:
        logger.error(f"Error serving favicon.ico: {e}")
        return '', 404

@app.route('/query', methods=['POST'])
def handle_query():
    # Check if user is logged in
    if not session.get('logged_in'):
        return jsonify({"error": "Authentication required"}), 401

    # Check if agent has identified themselves
    if not session.get('agent_identified') or not session.get('agent_name'):
        return jsonify({"error": "Please identify yourself before making queries"}), 401

    try:
        data = request.get_json()
        query = data.get('query', '').strip()

        if not query:
            return jsonify({"error": "Please provide a query"})

        # DEBUG: Log the query
        logger.info(f"Processing query: {query}")

        # --- UPDATE FUNCTION CALL ---
        # Call improved resource generation function
        related_resources = generate_related_resources_improved(query)
        # --- END UPDATE ---

        # DEBUG: Log what content was actually retrieved
        platform_resources_with_content = related_resources.get('platform_resources_with_content', [])
        logger.info(f"Retrieved {len(platform_resources_with_content)} resources with content")

        # NEW: Check if any content actually contains step instructions
        total_step_content = 0
        for resource in platform_resources_with_content:
            content = resource.get('content', '')
            step_indicators = ['step', 'navigate', 'click', 'select', 'go to']
            has_steps = any(indicator in content.lower() for indicator in step_indicators)
            if has_steps:
                total_step_content += 1
            logger.info(f"- {resource['title']}: {len(content)} chars, has_steps: {has_steps}")

        logger.info(f"Resources with actual step content: {total_step_content}")

        # Call the new Gemini API function
        ai_response = call_gemini_api(query, platform_resources_with_content)

        # Check if AI response contains an error
        is_error = ai_response.startswith("API Error") or ai_response.startswith("Error:")
        response_status = 'error' if is_error else 'success'

        # Generate Athena insights
        athena_insights = generate_athena_insights(query)

        # Generate suggested follow-up questions
        suggested_followups = generate_followup_suggestions(query, ai_response) if not is_error else []

        # Log agent activity
        agent_name = session.get('agent_name')  # No fallback - validation ensures this exists
        athena_used = athena_insights.get('has_data', False) if athena_insights else False
        log_agent_activity(
            agent_name=agent_name,
            query_text=query,
            response_status=response_status,
            resources_found=len(platform_resources_with_content),
            athena_used=athena_used
        )

        # If there's an error, return it in the standard error field format
        if is_error:
            return jsonify({
                "error": ai_response  # Return the error message string
            })

        # Store conversation context in session for follow-ups
        session['last_query'] = query
        session['last_response'] = ai_response
        session['last_resources'] = platform_resources_with_content

        return jsonify({
            "response": ai_response,
            "resources": related_resources,
            "athena_insights": athena_insights,
            "suggested_followups": suggested_followups
        })

    except Exception as e:
        # Log failed query
        agent_name = session.get('agent_name')  # No fallback - validation ensures this exists
        log_agent_activity(
            agent_name=agent_name,
            query_text=data.get('query', 'Unknown'),
            response_status='error'
        )
        print(f"Error in handle_query: {e}")
        return jsonify({"error": "An error occurred processing your request"})

@app.route('/followup', methods=['POST'])
def handle_followup():
    """Handle follow-up questions with conversation context"""
    # Check if user is logged in
    if not session.get('logged_in'):
        return jsonify({"error": "Authentication required"}), 401

    # Check if agent has identified themselves
    if not session.get('agent_identified') or not session.get('agent_name'):
        return jsonify({"error": "Please identify yourself before making queries"}), 401

    try:
        data = request.get_json()
        followup_query = data.get('query', '').strip()

        if not followup_query:
            return jsonify({"error": "Please provide a follow-up question"})

        # Get conversation context from session
        original_query = session.get('last_query', '')
        original_response = session.get('last_response', '')
        platform_resources = session.get('last_resources', None)

        # Log the follow-up query
        agent_name = session.get('agent_name')
        logger.info(f"Follow-up query from {agent_name}: {followup_query[:100]}")

        # Call Gemini API with conversation context
        if original_query and original_response:
            ai_response = call_gemini_api_with_conversation(
                followup_query,
                original_query,
                original_response,
                platform_resources
            )
        else:
            # Fallback to regular API if no context available
            logger.warning("No conversation context found, using standard API call")
            ai_response = call_gemini_api(followup_query)

        # Log the follow-up activity
        log_agent_activity(
            agent_name=agent_name,
            query_text=f"FOLLOW-UP: {followup_query}",
            response_status='success' if not ai_response.startswith("Error") else 'error',
            resources_found=0,
            athena_used=False
        )

        return jsonify({
            "response": ai_response
        })

    except Exception as e:
        logger.error(f"Error in handle_followup: {e}")
        return jsonify({"error": "An error occurred processing your follow-up"})


@app.route('/dashboard')
def dashboard():
    """Agent activity dashboard - Admin only"""
    # Check if user is logged in
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    # Check if user is admin
    if not session.get('is_admin', False):
        return "Access denied. Admin privileges required.", 403

    # Get activity statistics
    stats = get_activity_stats(days=30)

    if not stats:
        return "Error loading dashboard data", 500

    # Extract top keywords from queries
    keyword_counts = defaultdict(int)
    common_words = {'how', 'to', 'what', 'is', 'the', 'a', 'an', 'in', 'on', 'for', 'with', 'and', 'or', 'can', 'i', 'do', 'does'}

    for query in stats['all_queries']:
        words = re.findall(r'\b\w+\b', query.lower())
        for word in words:
            if len(word) > 3 and word not in common_words:
                keyword_counts[word] += 1

    top_keywords = sorted(keyword_counts.items(), key=lambda x: x[1], reverse=True)[:10]

    return render_template_string(DASHBOARD_TEMPLATE,
                                   stats=stats,
                                   top_keywords=top_keywords,
                                   agent_name=session.get('agent_name', 'Unknown'))

@app.route('/dashboard/delete-agent', methods=['POST'])
def delete_agent():
    """Delete all entries for a specific agent - Admin only"""
    # Check if user is logged in
    if not session.get('logged_in'):
        return jsonify({"error": "Authentication required"}), 401

    # Check if user is admin
    if not session.get('is_admin', False):
        return jsonify({"error": "Admin privileges required"}), 403

    try:
        data = request.get_json()
        agent_name = data.get('agent_name', '').strip()

        if not agent_name:
            return jsonify({"error": "Agent name is required"}), 400

        deleted_count = delete_agent_entries(agent_name)
        return jsonify({
            "success": True,
            "deleted_count": deleted_count,
            "message": f"Deleted {deleted_count} entries for {agent_name}"
        })
    except Exception as e:
        logger.error(f"Error in delete_agent: {e}")
        return jsonify({"error": "Failed to delete agent entries"}), 500

@app.route('/dashboard/export')
def export_queries():
    """Export all query data as CSV - Admin only"""
    # Check if user is logged in
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    # Check if user is admin
    if not session.get('is_admin', False):
        return "Access denied. Admin privileges required.", 403

    try:
        data = export_all_queries()

        # Create CSV in memory
        si = StringIO()
        writer = csv.writer(si)

        # Write header
        writer.writerow(['Agent Name', 'Query Text', 'Timestamp', 'Response Status', 'Resources Found', 'Athena Used'])

        # Write data
        for row in data:
            writer.writerow(row)

        # Create response
        output = make_response(si.getvalue())
        output.headers["Content-Disposition"] = f"attachment; filename=blueshift_support_queries_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        output.headers["Content-type"] = "text/csv"

        return output
    except Exception as e:
        logger.error(f"Error in export_queries: {e}")
        return "Error exporting data", 500


# Exact copy of production HTML with correct styling
LOGIN_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Support Bot - Login</title>
    <link rel="icon" type="image/png" sizes="32x32" href="/blueshift-favicon.png">
    <style>
        body {
            font-family: Arial, sans-serif;
            max-width: 400px;
            margin: 100px auto;
            padding: 20px;
            background: #f5f7fa;
        }
        .login-form {
            background: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        h1 {
            text-align: center;
            margin-bottom: 30px;
            color: #333;
            font-size: 24px;
            font-weight: 600;
        }
        .logo {
            height: 40px;
            vertical-align: middle;
            margin-right: 15px;
        }
        input {
            width: 100%;
            padding: 12px;
            margin: 10px 0;
            border: 1px solid #ddd;
            border-radius: 4px;
            box-sizing: border-box;
            font-size: 14px;
        }
        button {
            background-color: #2790FF;
            color: white;
            padding: 12px;
            width: 100%;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-size: 16px;
            font-weight: 500;
            transition: background-color 0.3s ease;
        }
        button:hover {
            background-color: #1976d2;
        }
        .error {
            color: #d73527;
            margin: 10px 0;
            padding: 10px;
            background: #ffeaea;
            border-radius: 4px;
            display: none;
        }
    </style>
</head>
<body>
    <div class="login-form">
        <h1>
            <img src="/blueshift-favicon.png" alt="Blueshift" class="logo">
            Support Bot
        </h1>
        <form id="loginForm">
            <input type="text" id="username" placeholder="Username" required>
            <input type="password" id="password" placeholder="Password" required>
            <button type="submit">Login</button>
        </form>

        <div id="error" class="error"></div>
    </div>

    <script>
        document.getElementById('loginForm').addEventListener('submit', function(e) {
            e.preventDefault();

            const username = document.getElementById('username').value;
            const password = document.getElementById('password').value;
            const errorDiv = document.getElementById('error');

            // Send login request to server
            fetch('/login', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    username: username,
                    password: password
                })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    // Clear any old session data from browser
                    sessionStorage.clear();
                    // Redirect to main app
                    window.location.href = '/';
                } else {
                    errorDiv.textContent = data.error || 'Invalid username or password';
                    errorDiv.style.display = 'block';
                    setTimeout(() => {
                        errorDiv.style.display = 'none';
                    }, 3000);
                }
            })
            .catch(error => {
                errorDiv.textContent = 'Login failed. Please try again.';
                errorDiv.style.display = 'block';
                setTimeout(() => {
                    errorDiv.style.display = 'none';
                }, 3000);
            });
        });

        // Clear error on input
        document.getElementById('username').addEventListener('input', function() {
            document.getElementById('error').style.display = 'none';
        });

        document.getElementById('password').addEventListener('input', function() {
            document.getElementById('error').style.display = 'none';
        });
    </script>
</body>
</html>
'''

MAIN_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Blueshift Support Bot - Interactive</title>
    <link rel="icon" type="image/png" sizes="32x32" href="/blueshift-favicon.png">
    <link rel="shortcut icon" href="/favicon.ico">
    <link rel="apple-touch-icon" sizes="32x32" href="/blueshift-favicon.png">
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
    <style>
        body {
            font-family: 'Calibri', sans-serif;
            font-size: 10pt;
            margin: 0;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
        }

        .container {
            max-width: 1000px;
            margin: 0 auto;
            background: white;
            margin-top: 40px;
            margin-bottom: 40px;
            padding: 50px;
            border-radius: 20px;
            box-shadow: 0 20px 40px rgba(0,0,0,0.1);
        }

        h1 {
            color: #2790FF;
            margin-bottom: 15px;
            text-align: center;
            font-size: 2.5em;
            font-weight: bold;
        }

        .search-container {
            text-align: center;
            margin-bottom: 40px;
        }

        input[type="text"] {
            width: 70%;
            padding: 18px 25px;
            border: 2px solid #e1e5e9;
            border-radius: 50px;
            font-size: 16px;
            outline: none;
            transition: all 0.3s ease;
            font-family: 'Calibri', sans-serif;
        }

        input[type="text"]:focus {
            border-color: #2790FF;
            box-shadow: 0 0 0 3px rgba(39, 144, 255, 0.1);
        }

        button {
            padding: 18px 35px;
            background: linear-gradient(45deg, #2790FF, #4da6ff);
            color: white;
            border: none;
            border-radius: 50px;
            font-size: 16px;
            cursor: pointer;
            margin-left: 15px;
            transition: all 0.3s ease;
            font-weight: 500;
            font-family: 'Calibri', sans-serif;
        }

        button:hover {
            transform: translateY(-2px);
            box-shadow: 0 8px 20px rgba(39, 144, 255, 0.3);
        }

        button:disabled {
            opacity: 0.6;
            cursor: not-allowed;
            transform: none;
        }

        .features {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 30px;
            margin-top: 50px;
        }

        .feature {
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            padding: 30px;
            border-radius: 15px;
            border-left: 5px solid #2790FF;
        }

        .feature h3 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.2em;
            line-height: 1.3;
        }

        .feature ul {
            list-style-type: none;
            padding: 0;
        }

        .feature li {
            padding: 8px 0;
            border-bottom: 1px solid rgba(39, 144, 255, 0.1);
        }

        .feature li:before {
            content: "✓";
            color: #2790FF;
            font-weight: bold;
            margin-right: 10px;
        }

        .response-section {
            background: #f8f9fa;
            border-radius: 15px;
            padding: 30px;
            margin: 30px 0;
            border-left: 5px solid #2790FF;
            max-height: 400px;
            overflow-y: auto;
        }

        .response-section h3 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.4em;
        }

        .response-content {
            line-height: 1.8;
            color: #555555 !important;
            font-weight: 400;
            font-size: 1.05em;
        }

        .response-content strong {
            font-weight: 700;
            color: #2c3e50;
        }

        .response-content h3 {
            color: #2790FF;
            margin-top: 20px;
            margin-bottom: 10px;
            font-size: 1.3em;
        }

        .response-content h4 {
            color: #2790FF;
            margin-top: 15px;
            margin-bottom: 8px;
            font-size: 1.1em;
        }

        /* INTERACTIVE FOLLOW-UP SECTION */
        .followup-section {
            background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
            border-radius: 15px;
            padding: 25px;
            margin: 25px 0;
            border-left: 5px solid #2790FF;
            display: none;
        }

        .followup-section h4 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.2em;
            margin-bottom: 10px;
        }

        .followup-section p.subtitle {
            margin: 0 0 15px 0;
            color: #666;
            font-size: 0.9rem;
        }

        .followup-suggestions {
            display: flex;
            flex-wrap: wrap;
            gap: 12px;
            margin-top: 15px;
        }

        .followup-chip {
            background: white;
            border: 2px solid #2790FF;
            color: #2790FF;
            padding: 12px 20px;
            border-radius: 25px;
            font-size: 0.9rem;
            cursor: pointer;
            transition: all 0.3s ease;
            font-family: 'Calibri', sans-serif;
            font-weight: 500;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }

        .followup-chip:hover {
            background: #2790FF;
            color: white;
            transform: translateY(-2px);
            box-shadow: 0 4px 12px rgba(39, 144, 255, 0.3);
        }

        .followup-chip:active {
            transform: translateY(0);
        }

        .followup-chip::before {
            content: "→";
            font-weight: bold;
            font-size: 1.1em;
        }

        /* Follow-up input container */
        .followup-container {
            display: flex;
            gap: 10px;
            margin-top: 15px;
        }

        .followup-response {
            margin-top: 20px;
            padding: 20px;
            background: rgba(255, 255, 255, 0.8);
            border-radius: 10px;
            border-left: 3px solid #2790FF;
            display: none;
            max-height: 300px;
            overflow-y: auto;
            white-space: pre-line;
        }

        .sources-section {
            margin-top: 30px;
        }

        .sources-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 25px;
            margin-top: 20px;
        }

        .source-category {
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            border-radius: 15px;
            padding: 25px;
            border-left: 5px solid #2790FF;
        }

        .source-category h4 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.2em;
        }

        .source-item {
            background: rgba(255, 255, 255, 0.7);
            border-radius: 8px;
            padding: 12px;
            margin: 8px 0;
            border-left: 3px solid #2790FF;
            font-size: 0.9em;
        }

        .source-item a {
            color: #000000;
            text-decoration: none;
            font-weight: 500;
        }

        .source-item a:hover {
            text-decoration: underline;
        }

        .loading {
            display: inline-block;
            width: 20px;
            height: 20px;
            border: 2px solid #f3f3f3;
            border-top: 2px solid #2790FF;
            border-radius: 50%;
            animation: spin 1s linear infinite;
        }

        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }

        .results-container {
            display: none;
        }

        .results-container.show + .features {
            display: none;
        }

        /* ATHENA SECTION STYLING */
        .athena-section {
            background: linear-gradient(135deg, #e3f2fd 0%, #bbdefb 100%);
            border-radius: 15px;
            padding: 25px;
            margin: 25px 0;
            border-left: 5px solid #2790FF;
        }

        .athena-section h3 {
            color: #2790FF;
            margin-top: 0;
            font-size: 1.3em;
        }

        .sql-query {
            background: #263238;
            color: #e0e0e0;
            padding: 15px;
            border-radius: 8px;
            font-family: 'Courier New', monospace;
            font-size: 12px;
            overflow-x: auto;
            margin: 15px 0;
        }

        .data-table {
            overflow-x: auto;
            margin: 15px 0;
        }

        .data-table table {
            width: 100%;
            border-collapse: collapse;
            min-width: 500px;
        }

        .data-table th, .data-table td {
            padding: 8px 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }

        .data-table th {
            background: #f5f5f5;
            font-weight: bold;
        }

        .athena-badge {
            background: linear-gradient(45deg, #2790FF, #4da6ff);
            color: white;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 0.8em;
            font-weight: bold;
            display: inline-block;
            margin-left: 10px;
        }

        /* Agent Identification Modal */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 0, 0, 0.7);
            z-index: 9999;
            justify-content: center;
            align-items: center;
        }

        .modal-overlay.show {
            display: flex;
        }

        .modal-content {
            background: white;
            padding: 40px;
            border-radius: 20px;
            box-shadow: 0 10px 40px rgba(0, 0, 0, 0.3);
            max-width: 400px;
            width: 90%;
            text-align: center;
        }

        .modal-content h2 {
            color: #2790FF;
            margin-bottom: 20px;
            font-size: 24px;
        }

        .modal-content p {
            color: #666;
            margin-bottom: 25px;
            line-height: 1.6;
        }

        .modal-content input {
            width: 100%;
            padding: 15px;
            border: 2px solid #e1e5e9;
            border-radius: 10px;
            font-size: 16px;
            margin-bottom: 20px;
            font-family: 'Calibri', sans-serif;
        }

        .modal-content input:focus {
            border-color: #2790FF;
            outline: none;
        }

        .modal-content button {
            width: 100%;
            padding: 15px;
            background: linear-gradient(45deg, #2790FF, #4da6ff);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            font-family: 'Calibri', sans-serif;
        }

        .modal-content button:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(39, 144, 255, 0.3);
        }

        .agent-badge-top {
            display: inline-block;
            background: linear-gradient(45deg, #2790FF, #4da6ff);
            color: white;
            padding: 8px 16px;
            border-radius: 20px;
            font-weight: 600;
            font-size: 14px;
            margin-left: 10px;
        }
    </style>
</head>
<body>
    <!-- Agent Identification Modal -->
    <div id="agentModal" class="modal-overlay">
        <div class="modal-content">
            <h2>👋 Welcome!</h2>
            <p>To help us track support activity, please enter your name:</p>
            <input type="text" id="agentNameInput" placeholder="Your name (e.g., Sarah, John)" autocomplete="off">
            <button id="submitAgentName">Continue</button>
        </div>
    </div>

    <div class="container">
        <div style="text-align: right; margin-bottom: 10px; display: flex; justify-content: space-between; align-items: center;">
            <span id="agentBadge" class="agent-badge-top" style="display: none;">👤 <span id="agentNameDisplay"></span></span>
            {% if is_admin %}
            <a href="/dashboard" style="display: inline-block; padding: 10px 20px; background: linear-gradient(45deg, #764ba2, #667eea); color: white; text-decoration: none; border-radius: 20px; font-weight: 600; font-size: 14px; transition: all 0.3s;">📊 View Dashboard</a>
            {% endif %}
        </div>
        <h1><img src="/blueshift-favicon.png" alt="Blueshift" style="height: 40px; vertical-align: middle; margin-right: 10px;">Blueshift Support Bot</h1>

        <div class="search-container">
            <input type="text" id="queryInput" placeholder="Enter your support question">
            <button id="searchBtn">Get Support Analysis</button>
        </div>

        <div id="resultsContainer" class="results-container">
            <div class="response-section">
                <div id="responseContent" class="response-content"></div>
            </div>

            <div class="followup-section" id="followupSection" style="display: block;">
                <h4>💬 Continue the conversation</h4>
                <p class="subtitle">Ask a follow-up question about this topic:</p>
                <div style="display: flex; gap: 10px; margin-top: 15px;">
                    <input type="text" id="followupInput" placeholder="Type your follow-up question..." style="flex: 1; padding: 12px 20px; border: 2px solid #2790FF; border-radius: 25px; font-size: 14px; outline: none; font-family: 'Calibri', sans-serif;">
                    <button id="followupBtn" style="background: linear-gradient(45deg, #2790FF, #4da6ff); color: white; padding: 12px 25px; border: none; border-radius: 25px; font-size: 14px; cursor: pointer; font-family: 'Calibri', sans-serif; font-weight: 600;">Ask</button>
                </div>
                <div id="followupResponse" style="margin-top: 20px; padding: 20px; background: rgba(255, 255, 255, 0.9); border-radius: 10px; border-left: 3px solid #2790FF; display: none;"></div>
            </div>

            <div id="athenaSection" class="athena-section" style="display: none;">
                <h3>📊 Suggested Query <span class="athena-badge">ATHENA</span></h3>
                <p><strong>Database:</strong> <span id="athenaDatabase" style="font-family: monospace; background: #f0f0f0; padding: 2px 6px; border-radius: 4px;"></span></p>
                <div><strong>Analysis:</strong></div>
                <div id="athenaExplanation" style="white-space: pre-line; margin-top: 8px; line-height: 1.6;"></div>

                <div style="margin: 15px 0;">
                    <label for="suggestedQuery" style="font-weight: bold; color: #2790FF;">Copy this query to Athena:</label>
                    <textarea id="suggestedQuery" class="sql-query" style="width: 100%; height: 120px; margin-top: 5px; font-family: 'Courier New', monospace; font-size: 12px; border: 2px solid #2790FF; border-radius: 8px; padding: 10px;" readonly placeholder="SQL query suggestion will appear here..."></textarea>
                    <p style="margin-top: 10px; color: #666; font-size: 0.9em;">💡 <strong>Instructions:</strong> Copy this query to AWS Athena console and customize with specific account_uuid, campaign_uuid, and date ranges for your support case.</p>
                </div>
            </div>

            <div class="sources-section">
                <h3>Related Resources</h3>
                <div id="sourcesGrid" class="sources-grid"></div>
            </div>

        </div>

        <div class="features">
            <div class="feature">
                <h3>🎫 Related JIRAs</h3>
                <ul>
                    <li>Links to relevant JIRA tickets and bugs</li>
                    <li>Known issues and their current status</li>
                    <li>Engineering updates and fixes</li>
                    <li>Product roadmap items</li>
                </ul>
            </div>

            <div class="feature">
                <h3>📚 Help Docs & APIs</h3>
                <ul>
                    <li>Official Blueshift help center articles</li>
                    <li>API documentation and endpoints</li>
                    <li>SDK integration guides</li>
                    <li>Setup and configuration instructions</li>
                </ul>
            </div>

            <div class="feature">
                <h3>🏢 Confluence</h3>
                <ul>
                    <li>Internal Confluence documentation</li>
                    <li>Team knowledge base articles</li>
                    <li>Troubleshooting runbooks</li>
                    <li>Engineering documentation</li>
                </ul>
            </div>

            <div class="feature">
                <h3>🎯 Zendesk</h3>
                <ul>
                    <li>Customer support ticket analysis</li>
                    <li>Similar issue resolutions</li>
                    <li>Support team responses</li>
                    <li>Escalation procedures</li>
                </ul>
            </div>
        </div>
    </div>

    <script>
        // Check if agent needs to identify themselves
        const agentIdentified = sessionStorage.getItem('agentIdentified');
        const agentName = sessionStorage.getItem('agentName');

        if (!agentIdentified) {
            document.getElementById('agentModal').classList.add('show');
        } else if (agentName) {
            // Show agent badge if already identified
            document.getElementById('agentNameDisplay').textContent = agentName;
            document.getElementById('agentBadge').style.display = 'inline-block';
        }

        // Handle agent identification
        document.getElementById('submitAgentName').addEventListener('click', function() {
            const agentName = document.getElementById('agentNameInput').value.trim();
            if (!agentName) {
                alert('Please enter your name');
                return;
            }

            fetch('/identify-agent', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ agent_name: agentName })
            })
            .then(response => response.json())
            .then(data => {
                if (data.success) {
                    sessionStorage.setItem('agentIdentified', 'true');
                    sessionStorage.setItem('agentName', agentName);
                    document.getElementById('agentModal').classList.remove('show');
                    // Show agent badge
                    document.getElementById('agentNameDisplay').textContent = agentName;
                    document.getElementById('agentBadge').style.display = 'inline-block';
                } else {
                    alert('Error: ' + (data.error || 'Failed to identify agent'));
                }
            })
            .catch(error => {
                alert('Error: ' + error);
            });
        });

        // Allow Enter key to submit
        document.getElementById('agentNameInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                document.getElementById('submitAgentName').click();
            }
        });

        document.getElementById('searchBtn').addEventListener('click', function() {
            const query = document.getElementById('queryInput').value.trim();
            if (!query) {
                alert('Please enter a question first');
                return;
            }

            // Show loading
            document.getElementById('searchBtn').innerHTML = '<span class="loading"></span> Analyzing...';
            document.getElementById('searchBtn').disabled = true;

            fetch('/query', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ query: query })
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    alert('Error: ' + data.error);
                    return;
                }

                // Show response with markdown rendering
                if (typeof marked !== 'undefined') {
                    document.getElementById('responseContent').innerHTML = marked.parse(data.response);
                } else {
                    document.getElementById('responseContent').textContent = data.response;
                }
                const resultsContainer = document.getElementById('resultsContainer');
                resultsContainer.style.display = 'block';
                resultsContainer.classList.add('show');

                // Show resources in 4-column grid
                showResources(data.resources);

                // Show Athena insights if available
                if (data.athena_insights) {
                    showAthenaInsights(data.athena_insights);
                }

                // Follow-up section is always visible now

                // Reset button
                document.getElementById('searchBtn').innerHTML = 'Get Support Analysis';
                document.getElementById('searchBtn').disabled = false;
            })
            .catch(error => {
                alert('Error: ' + error);
                document.getElementById('searchBtn').innerHTML = 'Get Support Analysis';
                document.getElementById('searchBtn').disabled = false;
            });
        });

        // Follow-up button handler
        document.getElementById('followupBtn').addEventListener('click', function() {
            const followupQuery = document.getElementById('followupInput').value.trim();
            if (!followupQuery) {
                alert('Please enter a follow-up question');
                return;
            }

            document.getElementById('followupBtn').innerHTML = 'Processing...';
            document.getElementById('followupBtn').disabled = true;

            fetch('/followup', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ query: followupQuery })
            })
            .then(response => response.json())
            .then(data => {
                if (data.error) {
                    alert('Error: ' + data.error);
                    return;
                }

                // Show response with markdown rendering
                const followupResponseDiv = document.getElementById('followupResponse');
                if (typeof marked !== 'undefined') {
                    followupResponseDiv.innerHTML = marked.parse(data.response);
                } else {
                    followupResponseDiv.textContent = data.response;
                }
                followupResponseDiv.style.display = 'block';
                document.getElementById('followupInput').value = '';

                document.getElementById('followupBtn').innerHTML = 'Ask';
                document.getElementById('followupBtn').disabled = false;
            })
            .catch(error => {
                alert('Error: ' + error);
                document.getElementById('followupBtn').innerHTML = 'Ask';
                document.getElementById('followupBtn').disabled = false;
            });
        });

        // Allow Enter key in follow-up input
        document.getElementById('followupInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                document.getElementById('followupBtn').click();
            }
        });

        function showResources(resources) {
            const sourcesGrid = document.getElementById('sourcesGrid');
            sourcesGrid.innerHTML = '';

            const categories = [
                { key: 'jira_tickets', title: '🎫 JIRA Tickets', icon: '🎫' },
                { key: 'help_docs', title: '📚 Help Docs & APIs', icon: '📚' },
                { key: 'confluence_docs', title: '🏢 Confluence Pages', icon: '🏢' },
                { key: 'support_tickets', title: '🎯 Zendesk', icon: '🎯' }
            ];

            categories.forEach(category => {
                const categoryDiv = document.createElement('div');
                categoryDiv.className = 'source-category';
                categoryDiv.innerHTML = `<h4>${category.title}</h4>`;

                // For Help Docs, combine both help_docs and api_docs
                let items = [];
                if (category.key === 'help_docs') {
                    items = [...(resources['help_docs'] || []), ...(resources['api_docs'] || [])];
                } else {
                    items = resources[category.key] || [];
                }

                items.forEach(item => {
                    const itemDiv = document.createElement('div');
                    itemDiv.className = 'source-item';
                    itemDiv.innerHTML = `<a href="${item.url}" target="_blank">${item.title}</a>`;
                    categoryDiv.appendChild(itemDiv);
                });

                sourcesGrid.appendChild(categoryDiv);
            });
        }

        // Allow Enter key to trigger search
        document.getElementById('queryInput').addEventListener('keypress', function(e) {
            if (e.key === 'Enter') {
                document.getElementById('searchBtn').click();
            }
        });

        // Removed old followup input event listener - now using interactive chips

        function showAthenaInsights(athenaData) {
            // Show the Athena section
            document.getElementById('athenaSection').style.display = 'block';

            // Set database
            document.getElementById('athenaDatabase').textContent = athenaData.database || 'default';

            // Set explanation
            document.getElementById('athenaExplanation').textContent = athenaData.explanation;

            // Set editable SQL query
            document.getElementById('suggestedQuery').value = athenaData.sql_query;

        }

    </script>
</body>
</html>
'''

DASHBOARD_TEMPLATE = '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Agent Activity Dashboard</title>
    <link rel="icon" type="image/png" sizes="32x32" href="/blueshift-favicon.png">
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }

        .dashboard-container {
            max-width: 1400px;
            margin: 0 auto;
        }

        .header {
            background: white;
            padding: 20px 30px;
            border-radius: 15px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            margin-bottom: 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .header h1 {
            color: #333;
            font-size: 28px;
        }

        .header-info {
            display: flex;
            gap: 20px;
            align-items: center;
        }

        .agent-badge {
            background: #667eea;
            color: white;
            padding: 8px 16px;
            border-radius: 20px;
            font-weight: 600;
        }

        .export-btn {
            background: #667eea;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 8px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
            font-size: 14px;
        }

        .export-btn:hover {
            background: #5568d3;
            transform: translateY(-2px);
        }

        .back-btn {
            background: #764ba2;
            color: white;
            padding: 10px 20px;
            border-radius: 8px;
            text-decoration: none;
            font-weight: 600;
            transition: all 0.3s;
        }

        .back-btn:hover {
            background: #5a3980;
            transform: translateY(-2px);
        }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }

        .stat-card {
            background: white;
            padding: 25px;
            border-radius: 15px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
        }

        .stat-card h3 {
            color: #667eea;
            font-size: 18px;
            margin-bottom: 15px;
        }

        .stat-number {
            font-size: 36px;
            font-weight: bold;
            color: #333;
            margin-bottom: 10px;
        }

        .stat-label {
            color: #666;
            font-size: 14px;
        }

        .agent-list {
            list-style: none;
        }

        .agent-item {
            display: flex;
            justify-content: space-between;
            padding: 10px 0;
            border-bottom: 1px solid #eee;
        }

        .agent-item:last-child {
            border-bottom: none;
        }

        .agent-name {
            font-weight: 600;
            color: #333;
        }

        .query-count {
            background: #667eea;
            color: white;
            padding: 4px 12px;
            border-radius: 12px;
            font-size: 14px;
            font-weight: 600;
        }

        .activity-section {
            background: white;
            padding: 30px;
            border-radius: 15px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1);
            margin-bottom: 30px;
        }

        .activity-section h2 {
            color: #333;
            margin-bottom: 20px;
            font-size: 24px;
        }

        .activity-table {
            width: 100%;
            border-collapse: collapse;
        }

        .activity-table th {
            background: #667eea;
            color: white;
            padding: 12px;
            text-align: left;
            font-weight: 600;
        }

        .activity-table td {
            padding: 12px;
            border-bottom: 1px solid #eee;
        }

        .activity-table tr:hover {
            background: #f8f9fa;
        }

        .status-badge {
            padding: 4px 10px;
            border-radius: 12px;
            font-size: 12px;
            font-weight: 600;
        }

        .status-success {
            background: #d4edda;
            color: #155724;
        }

        .status-error {
            background: #f8d7da;
            color: #721c24;
        }

        .keyword-list {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
        }

        .keyword-tag {
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 14px;
            font-weight: 600;
        }

        .keyword-count {
            background: rgba(255, 255, 255, 0.3);
            padding: 2px 8px;
            border-radius: 10px;
            margin-left: 6px;
        }

        .chart-container {
            margin-top: 20px;
        }

        .trend-bar {
            display: flex;
            align-items: center;
            margin-bottom: 12px;
        }

        .trend-date {
            width: 120px;
            font-weight: 600;
            color: #333;
        }

        .trend-bar-fill {
            background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
            height: 30px;
            border-radius: 15px;
            display: flex;
            align-items: center;
            padding: 0 12px;
            color: white;
            font-weight: 600;
            min-width: 40px;
        }

        .empty-state {
            text-align: center;
            padding: 40px;
            color: #666;
        }
    </style>
</head>
<body>
    <div class="dashboard-container">
        <div class="header">
            <h1>📊 Agent Activity Dashboard</h1>
            <div class="header-info">
                <span class="agent-badge">👤 {{ agent_name }}</span>
                <button onclick="exportQueries()" class="export-btn">📥 Export All Queries</button>
                <a href="/" class="back-btn">← Back to Support Bot</a>
            </div>
        </div>

        <div class="stats-grid">
            <div class="stat-card">
                <h3>Total Queries (Last 30 Days)</h3>
                <div class="stat-number">{{ stats.recent_activity|length }}</div>
                <div class="stat-label">across all agents</div>
            </div>

            <div class="stat-card">
                <h3>Active Agents</h3>
                <div class="stat-number">{{ stats.queries_by_agent|length }}</div>
                <div class="stat-label">agents have used the bot</div>
            </div>

            <div class="stat-card">
                <h3>Queries by Agent</h3>
                {% if stats.queries_by_agent %}
                <ul class="agent-list">
                    {% for agent, count in stats.queries_by_agent[:5] %}
                    <li class="agent-item">
                        <span class="agent-name">{{ agent if agent != 'Unknown Agent' else agent + ' ⚠️' }}</span>
                        <span class="query-count">{{ count }}</span>
                    </li>
                    {% endfor %}
                </ul>
                <p style="font-size: 11px; color: #999; margin-top: 10px;">⚠️ Unknown Agent = queries before tracking was enabled</p>
                {% else %}
                <div class="empty-state">No activity yet</div>
                {% endif %}
            </div>
        </div>

        <div class="activity-section">
            <h2>Top Search Topics</h2>
            {% if top_keywords %}
            <div class="keyword-list">
                {% for keyword, count in top_keywords %}
                <div class="keyword-tag">
                    {{ keyword }}
                    <span class="keyword-count">{{ count }}</span>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <div class="empty-state">No search data available</div>
            {% endif %}
        </div>

        <div class="activity-section">
            <h2>Monthly Trend by Agent (Last 6 Months)</h2>
            {% if stats.monthly_data %}
            <div class="chart-container">
                {% for month in stats.monthly_data|dictsort(reverse=true) %}
                <div style="margin-bottom: 25px;">
                    <h4 style="color: #667eea; margin-bottom: 10px;">{{ month[0] }}</h4>
                    {% for agent, count in month[1].items() %}
                    <div class="trend-bar" style="margin-bottom: 8px;">
                        <span class="trend-date" style="width: 180px;">{{ agent }}</span>
                        <div class="trend-bar-fill" style="width: {{ (count * 15) + 50 }}px; background: linear-gradient(90deg, #2790FF 0%, #4da6ff 100%);">
                            {{ count }}
                        </div>
                    </div>
                    {% endfor %}
                </div>
                {% endfor %}
            </div>
            {% else %}
            <div class="empty-state">No monthly data available</div>
            {% endif %}
        </div>

        <div class="activity-section">
            <h2>Daily Activity Trend (Last 14 Days)</h2>
            {% if stats.daily_trends %}
            <div class="chart-container">
                {% for date, count in stats.daily_trends[:14] %}
                <div class="trend-bar">
                    <span class="trend-date">{{ date }}</span>
                    <div class="trend-bar-fill" style="width: {{ (count * 10) + 40 }}px;">
                        {{ count }}
                    </div>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <div class="empty-state">No trend data available</div>
            {% endif %}
        </div>

        <div class="activity-section">
            <h2>Recent Activity</h2>
            {% if stats.recent_activity %}
            <table class="activity-table">
                <thead>
                    <tr>
                        <th>Agent</th>
                        <th>Query</th>
                        <th>Time</th>
                        <th>Status</th>
                    </tr>
                </thead>
                <tbody>
                    {% for agent, query, timestamp, status in stats.recent_activity[:20] %}
                    <tr>
                        <td><strong>{{ agent }}</strong></td>
                        <td>{{ query[:100] }}{% if query|length > 100 %}...{% endif %}</td>
                        <td>{{ timestamp }}</td>
                        <td>
                            <span class="status-badge {% if status == 'success' %}status-success{% else %}status-error{% endif %}">
                                {{ status }}
                            </span>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            {% else %}
            <div class="empty-state">No recent activity</div>
            {% endif %}
        </div>
    </div>

    <script>
        function exportQueries() {
            window.location.href = '/dashboard/export';
        }
    </script>
</body>
</html>
'''

if __name__ == '__main__':
    print("Starting Blueshift Support Bot with AWS Athena Integration...")
    port = int(os.environ.get('PORT', 8103))
    print(f"Visit: http://localhost:{port}")
    print(f"AWS Region: {AWS_REGION}")
    print(f"Athena Databases: {', '.join(ATHENA_DATABASES)}")
    print(f"Athena S3 Output: {ATHENA_S3_OUTPUT}")

    # Debug: Check environment variables
    print(f"\n=== Environment Variables Debug ===")
    print(f"JIRA_TOKEN: {'SET' if JIRA_TOKEN else 'NOT SET'}")
    print(f"JIRA_EMAIL: {'SET' if JIRA_EMAIL else 'NOT SET'}")
    print(f"CONFLUENCE_TOKEN: {'SET' if CONFLUENCE_TOKEN else 'NOT SET'}")
    print(f"CONFLUENCE_EMAIL: {'SET' if CONFLUENCE_EMAIL else 'NOT SET'}")
    print(f"ZENDESK_TOKEN: {'SET' if ZENDESK_TOKEN else 'NOT SET'}")
    print(f"ZENDESK_EMAIL: {'SET' if ZENDESK_EMAIL else 'NOT SET'}")
    print(f"ZENDESK_SUBDOMAIN: {'SET' if ZENDESK_SUBDOMAIN else 'NOT SET'}")
    print("=" * 40)
    
    # Print API status results
    print("\n=== External API Status ===")
    for api, status in API_STATUS.items():
        print(f"{api.upper()}: {'✅ Connected' if status else '❌ Failed/Missing Credentials'}")
    print("=" * 40)

    # ADDED Diagnostic Print Statement
    print("--- ATTEMPTING TO START FLASK APP ---")

    app.run(host='0.0.0.0', port=port, debug=True)

