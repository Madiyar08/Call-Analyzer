import math
import streamlit as st
import pandas as pd
import io
import os
from datetime import datetime
import requests

st.set_page_config(
    page_title="Отчёт операторов",
    page_icon="📊",
    layout="wide"
)

TIME_SLOTS = [
    "00:00-01:00", "01:00-02:00", "02:00-03:00", "03:00-04:00",
    "04:00-05:00", "05:00-06:00", "06:00-07:00", "07:00-08:00",
    "08:00-09:00", "09:00-10:00", "10:00-11:00", "11:00-12:00",
    "12:00-13:00", "13:00-14:00", "14:00-15:00", "15:00-16:00",
    "16:00-17:00", "17:00-18:00", "18:00-19:00", "19:00-20:00",
    "20:00-21:00", "21:00-22:00", "22:00-23:00", "23:00-00:00"
]


def get_google_sheets_credentials():
    """Fetch fresh credentials from Replit connector."""
    hostname = os.environ.get('REPLIT_CONNECTORS_HOSTNAME')
    repl_identity = os.environ.get('REPL_IDENTITY')
    web_repl_renewal = os.environ.get('WEB_REPL_RENEWAL')
    
    if repl_identity:
        x_replit_token = f'repl {repl_identity}'
    elif web_repl_renewal:
        x_replit_token = f'depl {web_repl_renewal}'
    else:
        return None, "Токен авторизации не найден"
    
    try:
        response = requests.get(
            f'https://{hostname}/api/v2/connection?include_secrets=true&connector_names=google-sheet',
            headers={
                'Accept': 'application/json',
                'X_REPLIT_TOKEN': x_replit_token
            }
        )
        data = response.json()
        connection_settings = data.get('items', [{}])[0] if data.get('items') else {}
        
        settings = connection_settings.get('settings', {})
        oauth_data = settings.get('oauth', {}).get('credentials', {})
        
        access_token = settings.get('access_token') or oauth_data.get('access_token')
        refresh_token = oauth_data.get('refresh_token')
        client_id = oauth_data.get('client_id')
        client_secret = oauth_data.get('client_secret')
        
        if not access_token:
            return None, "Google Sheets не подключен"
        
        return {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'client_id': client_id,
            'client_secret': client_secret,
            'token_uri': 'https://oauth2.googleapis.com/token'
        }, None
        
    except Exception as e:
        return None, f"Ошибка подключения: {str(e)}"


def get_google_sheets_client():
    """Get authenticated Google Sheets client using Replit connector."""
    creds_data, error = get_google_sheets_credentials()
    if error:
        return None, error
    
    try:
        import gspread
        from google.oauth2.credentials import Credentials
        
        creds = Credentials(
            token=creds_data['access_token'],
            refresh_token=creds_data.get('refresh_token'),
            token_uri=creds_data.get('token_uri'),
            client_id=creds_data.get('client_id'),
            client_secret=creds_data.get('client_secret'),
            scopes=[
                'https://www.googleapis.com/auth/spreadsheets',
                'https://www.googleapis.com/auth/drive.file'
            ]
        )
        
        client = gspread.authorize(creds)
        return client, None
        
    except Exception as e:
        return None, f"Ошибка подключения: {str(e)}"


def parse_call_data(df):
    """Parse raw call data from the manager's file format."""
    df = df.copy()
    
    if df.iloc[0, 0] == 'Дата':
        df = df.iloc[1:].reset_index(drop=True)
    
    col_mapping = {}
    original_cols = df.columns.tolist()
    
    for i, col in enumerate(original_cols):
        col_str = str(col).lower()
        if 'дата' in col_str and 'время' in col_str:
            col_mapping['date'] = col
            if i + 1 < len(original_cols):
                col_mapping['time'] = original_cols[i + 1]
            if i + 2 < len(original_cols):
                col_mapping['result'] = original_cols[i + 2]
    
    for i, col in enumerate(original_cols):
        col_str = str(col).lower()
        if 'первый оператор' in col_str:
            if i + 1 < len(original_cols):
                col_mapping['operator_name'] = original_cols[i + 1]
            break

    # Detect duration column (длительность разговора)
    duration_keywords = ['длительность', 'продолжительность', 'duration', 'talk time', 'время разговора', 'время обработки']
    for col in original_cols:
        col_str = str(col).lower()
        if any(kw in col_str for kw in duration_keywords):
            col_mapping['duration'] = col
            break

    return df, col_mapping


def get_hour_from_time(time_val):
    """Extract hour from time value."""
    if pd.isna(time_val):
        return None
    try:
        time_str = str(time_val)
        if ':' in time_str:
            parts = time_str.split(':')
            hour = int(parts[0])
            if 0 <= hour <= 23:
                return hour
        return None
    except:
        return None


def is_lost_call(result):
    """Check if call result indicates a lost call."""
    if pd.isna(result):
        return False
    result_lower = str(result).lower().strip()
    return result_lower == 'потерян' or 'потерян' in result_lower


def is_answered_call(result):
    """Check if call result indicates an answered call."""
    if pd.isna(result):
        return False
    result_lower = str(result).lower().strip()
    return 'обработан первым оператором' in result_lower or 'обработан вторым оператором' in result_lower or 'обработан последним оператором' in result_lower


def create_hourly_report(df, col_mapping):
    """Create hourly report from parsed call data."""
    df = df.copy()
    
    date_col = col_mapping.get('date')
    time_col = col_mapping.get('time')
    result_col = col_mapping.get('result')
    operator_col = col_mapping.get('operator_name')
    
    if not all([date_col, time_col, result_col]):
        return None, "Не удалось определить колонки данных"
    
    df['_hour'] = df[time_col].apply(get_hour_from_time)
    df['_is_lost'] = df[result_col].apply(is_lost_call)
    df['_is_answered'] = df[result_col].apply(is_answered_call)
    
    try:
        df['_date'] = pd.to_datetime(df[date_col], format='%d.%m.%Y', errors='coerce').dt.date
    except:
        df['_date'] = df[date_col]
    
    dates = sorted(df['_date'].dropna().unique())
    
    results = []
    
    for time_slot in TIME_SLOTS:
        row = {'Время': time_slot}
        start_hour = int(time_slot.split(':')[0])
        
        for date in dates:
            date_mask = df['_date'] == date
            hour_mask = df['_hour'] == start_hour
            filtered = df[date_mask & hour_mask]
            
            lost_count = int(filtered['_is_lost'].sum())
            answered_count = int(filtered['_is_answered'].sum())
            
            operator_count = 0
            if operator_col and operator_col in df.columns:
                operators = filtered[operator_col].dropna()
                operators = operators[operators.astype(str).str.strip() != '']
                operators = operators[operators.astype(str) != '+']
                operator_count = operators.nunique()
            
            date_str = date.strftime('%d.%m.%y') if hasattr(date, 'strftime') else str(date)
            
            row[f'{date_str}_Потерянные'] = lost_count
            row[f'{date_str}_Принятые'] = answered_count
            row[f'{date_str}_Оператор'] = operator_count
        
        results.append(row)
    
    totals = {'Время': 'Итог'}
    if results:
        for col in results[0].keys():
            if col != 'Время':
                totals[col] = sum(row[col] for row in results)
    results.append(totals)
    
    return pd.DataFrame(results), None


def create_operator_stats(df, col_mapping):
    """Create statistics per operator - answered and lost calls."""
    df = df.copy()
    
    result_col = col_mapping.get('result')
    operator_col = col_mapping.get('operator_name')
    
    if not result_col or not operator_col:
        return None
    
    df['_is_lost'] = df[result_col].apply(is_lost_call)
    df['_is_answered'] = df[result_col].apply(is_answered_call)
    
    operator_data = df[operator_col].dropna()
    operator_data = operator_data[operator_data.astype(str).str.strip() != '']
    operator_data = operator_data[operator_data.astype(str) != '+']
    operator_data = operator_data[~operator_data.astype(str).str.lower().str.contains('nan')]
    
    operators = operator_data.unique()
    
    results = []
    for operator in operators:
        operator_mask = df[operator_col] == operator
        operator_calls = df[operator_mask]
        
        answered = int(operator_calls['_is_answered'].sum())
        lost = int(operator_calls['_is_lost'].sum())
        total = answered + lost
        
        if total > 0:
            results.append({
                'Оператор': operator,
                'Принятые': answered,
                'Потерянные': lost,
                'Всего': total,
                '% принятых': round((answered / total) * 100, 1) if total > 0 else 0
            })
    
    if not results:
        return None
    
    result_df = pd.DataFrame(results)
    result_df = result_df.sort_values('Всего', ascending=False).reset_index(drop=True)
    
    return result_df


def create_operator_hourly_report(df, col_mapping):
    """Create hourly report per operator - calls per hour for each operator."""
    df = df.copy()
    
    date_col = col_mapping.get('date')
    time_col = col_mapping.get('time')
    result_col = col_mapping.get('result')
    operator_col = col_mapping.get('operator_name')
    
    if not all([date_col, time_col, result_col, operator_col]):
        return None
    
    df['_hour'] = df[time_col].apply(get_hour_from_time)
    df['_is_answered'] = df[result_col].apply(is_answered_call)
    
    try:
        df['_date'] = pd.to_datetime(df[date_col], format='%d.%m.%Y', errors='coerce').dt.date
    except:
        df['_date'] = df[date_col]
    
    operator_data = df[operator_col].dropna()
    operator_data = operator_data[operator_data.astype(str).str.strip() != '']
    operator_data = operator_data[operator_data.astype(str) != '+']
    operator_data = operator_data[~operator_data.astype(str).str.lower().str.contains('nan')]
    operators = sorted(operator_data.unique())
    
    if not operators:
        return None
    
    dates = sorted(df['_date'].dropna().unique())
    
    all_reports = {}
    
    for date in dates:
        date_str = date.strftime('%d.%m.%Y') if hasattr(date, 'strftime') else str(date)
        results = []
        
        for time_slot in TIME_SLOTS:
            row = {date_str: time_slot}
            start_hour = int(time_slot.split(':')[0])
            
            date_mask = df['_date'] == date
            hour_mask = df['_hour'] == start_hour
            
            for operator in operators:
                operator_mask = df[operator_col] == operator
                filtered = df[date_mask & hour_mask & operator_mask]
                call_count = int(filtered['_is_answered'].sum())
                row[operator] = call_count
            
            results.append(row)
        
        totals = {date_str: 'Итог'}
        for operator in operators:
            totals[operator] = sum(row[operator] for row in results)
        results.append(totals)
        
        all_reports[date_str] = pd.DataFrame(results)
    
    return all_reports


def parse_duration_seconds(val):
    """Parse duration value to seconds. Supports HH:MM:SS, MM:SS, or raw seconds."""
    if pd.isna(val):
        return None
    s = str(val).strip()
    if ':' in s:
        parts = s.split(':')
        try:
            if len(parts) == 3:
                return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(float(parts[2]))
            elif len(parts) == 2:
                return int(parts[0]) * 60 + int(float(parts[1]))
        except:
            return None
    try:
        return float(s)
    except:
        return None


def create_fte_analysis(df, col_mapping):
    """
    Create FTE and Occupancy analysis per hour across all dates.
    """
    df = df.copy()

    date_col = col_mapping.get('date')
    time_col = col_mapping.get('time')
    result_col = col_mapping.get('result')
    operator_col = col_mapping.get('operator_name')
    duration_col = col_mapping.get('duration')

    if not all([date_col, time_col, result_col]):
        return None, "Не хватает колонок (дата/время/результат)"

    df['_hour'] = df[time_col].apply(get_hour_from_time)
    df['_is_answered'] = df[result_col].apply(is_answered_call)

    try:
        df['_date'] = pd.to_datetime(df[date_col], format='%d.%m.%Y', errors='coerce').dt.date
    except:
        df['_date'] = df[date_col]

    if duration_col and duration_col in df.columns:
        df['_duration_sec'] = df[duration_col].apply(parse_duration_seconds)
    else:
        df['_duration_sec'] = None

    dates = sorted(df['_date'].dropna().unique())
    rows = []

    for date in dates:
        date_str = date.strftime('%d.%m.%Y') if hasattr(date, 'strftime') else str(date)
        date_mask = df['_date'] == date

        for hour in range(24):
            hour_mask = df['_hour'] == hour
            slot = df[date_mask & hour_mask]
            answered_slot = slot[slot['_is_answered']]

            answered_count = int(answered_slot.shape[0])

            actual_ops = 0
            if operator_col and operator_col in df.columns:
                ops = slot[operator_col].dropna()
                ops = ops[ops.astype(str).str.strip().ne('') & ops.astype(str).ne('+')]
                actual_ops = ops.nunique()

            aht = None
            if '_duration_sec' in df.columns and df['_duration_sec'].notna().any():
                durations = answered_slot['_duration_sec'].dropna()
                if len(durations) > 0:
                    aht = durations.mean()

            required_fte = None
            if aht and answered_count > 0:
                required_fte = round((answered_count * aht) / 3600, 2)

            occupancy = None
            if aht and actual_ops > 0 and answered_count > 0:
                occupancy = round((answered_count * aht) / (actual_ops * 3600) * 100, 1)

            status = ''
            if occupancy is not None:
                if occupancy > 90:
                    status = '🔴 Перегрузка'
                elif occupancy > 70:
                    status = '🟡 Высокая'
                elif occupancy > 40:
                    status = '🟢 Норма'
                elif answered_count > 0:
                    status = '🔵 Недозагруженность'

            rows.append({
                'Дата': date_str,
                'Час': f"{hour:02d}:00–{(hour+1)%24:02d}:00",
                'Принятые': answered_count,
                'Операторов (факт)': actual_ops,
                'AHT (сек)': round(aht, 0) if aht else None,
                'Required FTE': required_fte,
                'Occupancy %': occupancy,
                'Статус нагрузки': status,
            })

    result_df = pd.DataFrame(rows)
    result_df = result_df[
        (result_df['Принятые'] > 0) | (result_df['Операторов (факт)'] > 0)
    ].reset_index(drop=True)

    return result_df, None


def generate_staffing_recommendations(fte_df):
    """
    Analyse FTE data and return a structured dict of staffing recommendations.
    Works even without Occupancy (falls back to Required vs Actual comparison).
    """
    if fte_df is None or fte_df.empty:
        return None

    has_occ = fte_df['Occupancy %'].notna().any()
    has_req = fte_df['Required FTE'].notna().any()

    recs = {}

    # ── 1. Overall assessment ────────────────────────────────────────────
    active = fte_df[fte_df['Принятые'] > 0].copy()
    if active.empty:
        return None

    avg_actual = active['Операторов (факт)'].mean()

    if has_req:
        active_req = active.dropna(subset=['Required FTE'])
        avg_req = active_req['Required FTE'].mean() if not active_req.empty else None
    else:
        avg_req = None

    if has_occ:
        active_occ = active.dropna(subset=['Occupancy %'])
        avg_occ = active_occ['Occupancy %'].mean() if not active_occ.empty else None
    else:
        avg_occ = None

    recs['avg_actual'] = round(avg_actual, 1)
    recs['avg_req'] = round(avg_req, 2) if avg_req else None
    recs['avg_occ'] = round(avg_occ, 1) if avg_occ else None

    if avg_req:
        diff = avg_actual - avg_req
        recs['overall_diff'] = round(diff, 1)
        if diff > 0.5:
            recs['overall_status'] = 'excess'
            recs['overall_msg'] = (
                f"В среднем операторов **больше, чем нужно** на **{diff:.1f} чел.**  "
                f"(факт: {avg_actual:.1f}, нужно: {avg_req:.2f}).  "
                f"Можно сократить смену или перераспределить часть операторов на другие часы."
            )
        elif diff < -0.5:
            recs['overall_status'] = 'shortage'
            recs['overall_msg'] = (
                f"В среднем операторов **не хватает на {abs(diff):.1f} чел.**  "
                f"(факт: {avg_actual:.1f}, нужно: {avg_req:.2f}).  "
                f"Рекомендуется добавить операторов или перенести часть нагрузки."
            )
        else:
            recs['overall_status'] = 'ok'
            recs['overall_msg'] = (
                f"Общее количество операторов соответствует нагрузке  "
                f"(факт: {avg_actual:.1f}, нужно: {avg_req:.2f})."
            )
    elif avg_occ:
        if avg_occ > 85:
            recs['overall_status'] = 'shortage'
            recs['overall_msg'] = f"Средняя загрузка **{avg_occ:.1f}%** — операторы перегружены. Нужно добавить людей."
        elif avg_occ < 45:
            recs['overall_status'] = 'excess'
            recs['overall_msg'] = f"Средняя загрузка **{avg_occ:.1f}%** — операторов слишком много. Можно сократить."
        else:
            recs['overall_status'] = 'ok'
            recs['overall_msg'] = f"Средняя загрузка **{avg_occ:.1f}%** — нагрузка в норме."
    else:
        recs['overall_status'] = 'nodata'
        recs['overall_msg'] = "Нет данных о длительности звонков — точные рекомендации недоступны."

    # ── 2. Problem hours (per hour slot, averaged across dates) ──────────
    hour_group = active.groupby('Час').agg(
        avg_actual=('Операторов (факт)', 'mean'),
        avg_req=('Required FTE', 'mean'),
        avg_occ=('Occupancy %', 'mean'),
        total_calls=('Принятые', 'sum'),
    ).reset_index()

    shortage_hours = []
    excess_hours = []

    for _, row in hour_group.iterrows():
        hour = row['Час']
        act = row['avg_actual']
        req = row['avg_req']
        occ = row['avg_occ']
        calls = row['total_calls']

        if pd.notna(req):
            diff = act - req
            need = max(0, math.ceil(req - act))
            remove = max(0, math.floor(act - req))
            if diff < -0.3 and calls > 0:
                shortage_hours.append({
                    'Час': hour,
                    'Факт': round(act, 1),
                    'Нужно': round(req, 2),
                    'Добавить': need,
                    'Occupancy %': round(occ, 1) if pd.notna(occ) else None,
                })
            elif diff > 0.5 and calls > 0:
                excess_hours.append({
                    'Час': hour,
                    'Факт': round(act, 1),
                    'Нужно': round(req, 2),
                    'Убрать': remove,
                    'Occupancy %': round(occ, 1) if pd.notna(occ) else None,
                })
        elif pd.notna(occ):
            if occ > 90 and calls > 0:
                shortage_hours.append({
                    'Час': hour,
                    'Факт': round(act, 1),
                    'Нужно': '?',
                    'Добавить': '≥1',
                    'Occupancy %': round(occ, 1),
                })
            elif occ < 40 and calls > 0:
                excess_hours.append({
                    'Час': hour,
                    'Факт': round(act, 1),
                    'Нужно': '?',
                    'Убрать': '≥1',
                    'Occupancy %': round(occ, 1),
                })

    recs['shortage_hours'] = shortage_hours
    recs['excess_hours'] = excess_hours

    # ── 3. Peak hours (top-3 by calls) ───────────────────────────────────
    peak = hour_group.nlargest(3, 'total_calls')[['Час', 'total_calls', 'avg_actual', 'avg_req', 'avg_occ']]
    recs['peak_hours'] = peak.to_dict('records')

    # ── 4. Operator-level recommendations ────────────────────────────────
    if has_occ:
        date_summary = active.groupby('Дата').agg(
            avg_occ=('Occupancy %', 'mean'),
            avg_actual=('Операторов (факт)', 'mean'),
            avg_req=('Required FTE', 'mean'),
        ).reset_index()
        recs['date_summary'] = date_summary.to_dict('records')
    else:
        recs['date_summary'] = []

    return recs


def export_to_google_sheets(df, spreadsheet_name):
    """Export dataframe to Google Sheets."""
    client, error = get_google_sheets_client()
    if error:
        return None, error
    
    try:
        try:
            spreadsheet = client.open(spreadsheet_name)
            worksheet = spreadsheet.sheet1
            worksheet.clear()
        except:
            spreadsheet = client.create(spreadsheet_name)
            worksheet = spreadsheet.sheet1
        
        headers = df.columns.tolist()
        worksheet.append_row(headers)
        
        for _, row in df.iterrows():
            row_values = [str(v) if pd.notna(v) else "" for v in row.tolist()]
            worksheet.append_row(row_values)
        
        return spreadsheet.url, None
        
    except Exception as e:
        return None, f"Ошибка экспорта: {str(e)}"


def convert_df_to_excel(df):
    """Convert dataframe to Excel bytes."""
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name='Отчёт')
        
        workbook = writer.book
        worksheet = writer.sheets['Отчёт']
        
        header_format = workbook.add_format({
            'bold': True,
            'bg_color': '#4CAF50',
            'font_color': 'white',
            'border': 1
        })
        
        for col_num, value in enumerate(df.columns.values):
            worksheet.write(0, col_num, value, header_format)
            worksheet.set_column(col_num, col_num, 15)
    
    return output.getvalue()


st.title("📊 Отчёт операторов по звонкам")
st.markdown("Автоматизация ежедневного отчёта по звонкам операторов")

uploaded_file = st.file_uploader(
    "📁 Загрузите файл от менеджера (.xls или .xlsx)",
    type=['xls', 'xlsx'],
    help="Поддерживаются форматы Excel: .xls и .xlsx"
)

if uploaded_file:
    try:
        if uploaded_file.name.endswith('.xls'):
            df = pd.read_excel(uploaded_file, engine='xlrd')
        else:
            df = pd.read_excel(uploaded_file, engine='openpyxl')
        
        st.success(f"Файл загружен: {uploaded_file.name} ({len(df)} записей)")
        
        with st.expander("📋 Исходные данные (первые 20 строк)", expanded=False):
            st.dataframe(df.head(20), use_container_width=True)
        
        parsed_df, col_mapping = parse_call_data(df)
        
        st.info(f"Найдены колонки: Дата={col_mapping.get('date')}, Время={col_mapping.get('time')}, Результат={col_mapping.get('result')}")
        
        report_df, error = create_hourly_report(parsed_df, col_mapping)
        
        if error:
            st.error(f"Ошибка: {error}")
        else:
            st.subheader("📊 Отчёт по часам")
            st.dataframe(report_df, use_container_width=True, height=700)
            
            st.subheader("📈 Статистика")
            
            numeric_cols = [c for c in report_df.columns if c != 'Время']
            totals_row = report_df[report_df['Время'] == 'Итог']
            
            lost_cols = [c for c in numeric_cols if 'Потерянные' in c]
            answered_cols = [c for c in numeric_cols if 'Принятые' in c]
            
            if not totals_row.empty:
                col1, col2, col3 = st.columns(3)
                
                if lost_cols:
                    total_lost = totals_row[lost_cols].sum(axis=1).values[0]
                    col1.metric("🔴 Всего потеряно", int(total_lost))
                
                if answered_cols:
                    total_answered = totals_row[answered_cols].sum(axis=1).values[0]
                    col2.metric("🟢 Всего принято", int(total_answered))
                
                if lost_cols and answered_cols:
                    total_calls = total_lost + total_answered
                    if total_calls > 0:
                        success_rate = (total_answered / total_calls) * 100
                        col3.metric("📊 Процент ответов", f"{success_rate:.1f}%")
            
            st.subheader("📊 Диаграммы")
            
            chart_data = report_df[report_df['Время'] != 'Итог'].copy()
            
            if lost_cols and answered_cols:
                chart_df = pd.DataFrame({
                    'Время': chart_data['Время'],
                    'Потерянные': chart_data[lost_cols].sum(axis=1),
                    'Принятые': chart_data[answered_cols].sum(axis=1)
                })
                chart_df = chart_df.set_index('Время')
                
                st.markdown("#### Звонки по часам (все даты)")
                st.bar_chart(chart_df, color=["#ff4b4b", "#4CAF50"])
                
                st.markdown("#### Соотношение принятых и потерянных")
                pie_data = pd.DataFrame({
                    'Тип': ['Принятые', 'Потерянные'],
                    'Количество': [int(total_answered), int(total_lost)]
                })
                
                col1, col2 = st.columns([1, 2])
                with col1:
                    st.dataframe(pie_data, hide_index=True)
                with col2:
                    chart_df_pie = pie_data.set_index('Тип')
                    st.bar_chart(chart_df_pie, color=["#4CAF50"])
            
            st.subheader("👤 Статистика по операторам")
            
            operator_stats = create_operator_stats(parsed_df, col_mapping)
            
            if operator_stats is not None and not operator_stats.empty:
                st.dataframe(operator_stats, use_container_width=True, hide_index=True)
                
                st.markdown("#### Звонки по операторам")
                op_chart_df = operator_stats[['Оператор', 'Принятые', 'Потерянные']].copy()
                op_chart_df = op_chart_df.set_index('Оператор')
                st.bar_chart(op_chart_df, color=["#4CAF50", "#ff4b4b"])
            else:
                st.warning("Не удалось определить статистику по операторам. Проверьте наличие колонки с именами операторов в файле.")
            
            st.subheader("📅 Звонки операторов по часам")
            
            operator_hourly = create_operator_hourly_report(parsed_df, col_mapping)
            
            if operator_hourly:
                for date_str, date_df in operator_hourly.items():
                    st.markdown(f"#### {date_str}")
                    st.dataframe(date_df, use_container_width=True, hide_index=True)
            else:
                st.warning("Не удалось сформировать почасовой отчёт по операторам.")
            
            # ── FTE & OCCUPANCY ANALYSIS ──────────────────────────────────
            st.subheader("⚡ Анализ FTE и нагрузки операторов")

            duration_col = col_mapping.get('duration')
            if not duration_col:
                st.warning(
                    "Колонка с длительностью звонка не найдена автоматически. "
                    "Выберите её вручную:"
                )
                all_cols = [c for c in parsed_df.columns if c not in col_mapping.values()]
                duration_manual = st.selectbox(
                    "Колонка с длительностью (сек / ЧЧ:ММ:СС):",
                    options=["— не указывать —"] + list(parsed_df.columns),
                    key="duration_col_select"
                )
                if duration_manual and duration_manual != "— не указывать —":
                    col_mapping['duration'] = duration_manual

            fte_df, fte_error = create_fte_analysis(parsed_df, col_mapping)

            if fte_error:
                st.error(f"Ошибка FTE: {fte_error}")
            elif fte_df is None or fte_df.empty:
                st.info("Нет данных для расчёта FTE.")
            else:
                has_aht = fte_df['AHT (сек)'].notna().any()
                has_occ = fte_df['Occupancy %'].notna().any()

                if not has_aht:
                    st.info(
                        "Длительность звонков не найдена — AHT и Occupancy не рассчитаны. "
                        "Укажите колонку выше, чтобы получить полный анализ."
                    )

                # Summary metrics
                col1, col2, col3, col4 = st.columns(4)
                avg_aht = fte_df['AHT (сек)'].mean()
                avg_occ = fte_df['Occupancy %'].mean()
                avg_req = fte_df['Required FTE'].mean()
                avg_act = fte_df['Операторов (факт)'].mean()

                col1.metric("⏱ Средний AHT", f"{avg_aht:.0f} сек" if pd.notna(avg_aht) else "—")
                col2.metric("📊 Средняя загрузка", f"{avg_occ:.1f}%" if pd.notna(avg_occ) else "—")
                col3.metric("👥 Required FTE (ср.)", f"{avg_req:.2f}" if pd.notna(avg_req) else "—")
                col4.metric("👤 Actual FTE (ср.)", f"{avg_act:.1f}" if pd.notna(avg_act) else "—")

                # Filter controls
                st.markdown("#### 📋 Детальная таблица по часам")
                dates_available = sorted(fte_df['Дата'].unique())
                selected_dates = st.multiselect(
                    "Фильтр по дате:",
                    options=dates_available,
                    default=dates_available,
                    key="fte_date_filter"
                )
                filtered_fte = fte_df[fte_df['Дата'].isin(selected_dates)] if selected_dates else fte_df

                # Color occupancy column
                def color_occupancy(val):
                    if pd.isna(val):
                        return ''
                    if val > 90:
                        return 'background-color: #ffcccc'
                    elif val > 70:
                        return 'background-color: #fff3cd'
                    elif val > 40:
                        return 'background-color: #d4edda'
                    return 'background-color: #cce5ff'

                styler = filtered_fte.style
                try:
                    styler = styler.map(color_occupancy, subset=['Occupancy %'])
                except AttributeError:
                    styler = styler.applymap(color_occupancy, subset=['Occupancy %'])
                styled = styler.format({
                    'AHT (сек)': lambda x: f"{x:.0f}" if pd.notna(x) else "—",
                    'Required FTE': lambda x: f"{x:.2f}" if pd.notna(x) else "—",
                    'Occupancy %': lambda x: f"{x:.1f}%" if pd.notna(x) else "—",
                })
                st.dataframe(styled, use_container_width=True, hide_index=True)

                # Charts
                if has_occ and has_aht:
                    st.markdown("#### 📈 Загрузка (Occupancy %) по часам")
                    chart_occ = filtered_fte[['Час', 'Occupancy %', 'Дата']].dropna(subset=['Occupancy %'])
                    if not chart_occ.empty:
                        pivot_occ = chart_occ.pivot_table(
                            index='Час', columns='Дата', values='Occupancy %', aggfunc='mean'
                        )
                        st.line_chart(pivot_occ)

                if has_aht:
                    st.markdown("#### 👥 Required FTE vs Actual FTE по часам")
                    chart_fte = filtered_fte[['Час', 'Required FTE', 'Операторов (факт)']].dropna(subset=['Required FTE'])
                    if not chart_fte.empty:
                        pivot_fte = chart_fte.groupby('Час')[['Required FTE', 'Операторов (факт)']].mean()
                        pivot_fte.columns = ['Required FTE (среднее)', 'Actual Operators (среднее)']
                        st.line_chart(pivot_fte)

                # Overload summary
                st.markdown("#### 🔴 Часы с перегрузкой (Occupancy > 90%)")
                overloaded = filtered_fte[filtered_fte['Occupancy %'] > 90]
                if overloaded.empty:
                    st.success("Перегруженных часов нет!")
                else:
                    st.dataframe(overloaded[['Дата', 'Час', 'Принятые', 'Операторов (факт)', 'Required FTE', 'Occupancy %']], use_container_width=True, hide_index=True)

                # Excel download for FTE
                fte_excel = convert_df_to_excel(filtered_fte)
                st.download_button(
                    label="📥 Скачать FTE-анализ в Excel",
                    data=fte_excel,
                    file_name=f"fte_анализ_{datetime.now().strftime('%d.%m.%Y')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

                # ── STAFFING RECOMMENDATIONS ──────────────────────────────
                st.subheader("💡 Выводы и рекомендации по штату")

                recs = generate_staffing_recommendations(filtered_fte)

                if recs is None:
                    st.info("Недостаточно данных для формирования рекомендаций.")
                else:
                    # Overall verdict
                    status = recs.get('overall_status', 'nodata')
                    msg = recs.get('overall_msg', '')

                    if status == 'shortage':
                        st.error(f"🔴 **Общий вывод:** {msg}")
                    elif status == 'excess':
                        st.warning(f"🟡 **Общий вывод:** {msg}")
                    elif status == 'ok':
                        st.success(f"🟢 **Общий вывод:** {msg}")
                    else:
                        st.info(f"ℹ️ **Общий вывод:** {msg}")

                    # Peak hours
                    peak = recs.get('peak_hours', [])
                    if peak:
                        st.markdown("#### 📈 Пиковые часы нагрузки")
                        peak_lines = []
                        for p in peak:
                            occ_str = f", загрузка {p['avg_occ']:.0f}%" if pd.notna(p.get('avg_occ')) else ""
                            req_str = f", нужно {p['avg_req']:.1f} оп." if pd.notna(p.get('avg_req')) else ""
                            peak_lines.append(
                                f"- **{p['Час']}** — {int(p['total_calls'])} звонков, "
                                f"факт {p['avg_actual']:.1f} оп.{req_str}{occ_str}"
                            )
                        st.markdown("\n".join(peak_lines))

                    # Hours where operators are short
                    shortage = recs.get('shortage_hours', [])
                    if shortage:
                        st.markdown("#### 🔴 Часы нехватки операторов — нужно **добавить**")
                        rows_s = []
                        for s in shortage:
                            occ_str = f"{s['Occupancy %']:.1f}%" if s.get('Occupancy %') is not None else "—"
                            rows_s.append({
                                'Час': s['Час'],
                                'Факт (чел.)': s['Факт'],
                                'Нужно (чел.)': s['Нужно'],
                                'Добавить (чел.)': s['Добавить'],
                                'Occupancy %': occ_str,
                            })
                        st.dataframe(pd.DataFrame(rows_s), use_container_width=True, hide_index=True)
                    else:
                        st.success("✅ Часов с нехваткой операторов нет.")

                    # Hours where operators are excess
                    excess = recs.get('excess_hours', [])
                    if excess:
                        st.markdown("#### 🔵 Часы избытка операторов — можно **убрать**")
                        rows_e = []
                        for e in excess:
                            occ_str = f"{e['Occupancy %']:.1f}%" if e.get('Occupancy %') is not None else "—"
                            rows_e.append({
                                'Час': e['Час'],
                                'Факт (чел.)': e['Факт'],
                                'Нужно (чел.)': e['Нужно'],
                                'Убрать (чел.)': e['Убрать'],
                                'Occupancy %': occ_str,
                            })
                        st.dataframe(pd.DataFrame(rows_e), use_container_width=True, hide_index=True)
                    else:
                        st.success("✅ Лишних операторов нет.")

                    # By-date summary
                    date_sum = recs.get('date_summary', [])
                    if date_sum:
                        st.markdown("#### 📅 Итог по дням")
                        date_rows = []
                        for d in date_sum:
                            avg_occ = d.get('avg_occ')
                            avg_req = d.get('avg_req')
                            avg_act = d.get('avg_actual')

                            if pd.notna(avg_occ):
                                if avg_occ > 85:
                                    verdict = "🔴 Перегрузка — добавить операторов"
                                elif avg_occ > 70:
                                    verdict = "🟡 Высокая нагрузка — близко к пределу"
                                elif avg_occ > 40:
                                    verdict = "🟢 Норма"
                                else:
                                    verdict = "🔵 Недозагруженность — можно сократить"
                            else:
                                verdict = "—"

                            diff_str = "—"
                            if pd.notna(avg_req) and pd.notna(avg_act):
                                diff = avg_act - avg_req
                                if diff > 0.5:
                                    diff_str = f"Избыток +{diff:.1f} чел."
                                elif diff < -0.5:
                                    diff_str = f"Нехватка {diff:.1f} чел."
                                else:
                                    diff_str = "Баланс"

                            date_rows.append({
                                'Дата': d['Дата'],
                                'Ср. загрузка': f"{avg_occ:.1f}%" if pd.notna(avg_occ) else "—",
                                'Факт (ср.)': f"{avg_act:.1f}" if pd.notna(avg_act) else "—",
                                'Нужно (ср.)': f"{avg_req:.1f}" if pd.notna(avg_req) else "—",
                                'Баланс': diff_str,
                                'Вывод': verdict,
                            })
                        st.dataframe(pd.DataFrame(date_rows), use_container_width=True, hide_index=True)

            # ─────────────────────────────────────────────────────────────
            st.subheader("📤 Экспорт")
            
            col1, col2 = st.columns(2)
            
            with col1:
                st.markdown("### 💾 Скачать Excel")
                excel_data = convert_df_to_excel(report_df)
                st.download_button(
                    label="📥 Скачать Excel файл",
                    data=excel_data,
                    file_name=f"отчет_операторов_{datetime.now().strftime('%d.%m.%Y')}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            
            with col2:
                st.markdown("### 📊 Экспорт в Google Sheets")
                sheet_name = st.text_input(
                    "Название таблицы:",
                    value=f"Отчёт операторов {datetime.now().strftime('%d.%m.%Y')}"
                )
                
                if st.button("📤 Экспортировать в Google Sheets"):
                    with st.spinner("Экспортируем в Google Sheets..."):
                        url, error = export_to_google_sheets(report_df, sheet_name)
                        if error:
                            st.error(f"❌ {error}")
                        else:
                            st.success("Успешно экспортировано!")
                            st.markdown(f"[🔗 Открыть таблицу]({url})")
                        
    except Exception as e:
        st.error(f"Ошибка при обработке файла: {str(e)}")
        import traceback
        st.code(traceback.format_exc())

# ── METHODOLOGY BLOCK — always visible at the bottom ──────────────────────────
st.divider()
with st.expander("📐 Методология расчётов — как считаются все показатели", expanded=False):
    st.markdown("""
## 📞 Основные показатели звонков

### Принятые звонки
Звонок считается **принятым**, если в колонке «Результат» содержится одно из значений:
- `Обработан первым оператором`
- `Обработан вторым оператором`
- `Обработан последним оператором`

### Потерянные звонки
Звонок считается **потерянным**, если в колонке «Результат» содержится значение `Потерян`.

### Процент ответов (Service Level по объёму)
```
% принятых = Принятые / (Принятые + Потерянные) × 100%
```

---

## ⏱ AHT — Average Handle Time (среднее время обработки)

AHT считается из колонки с длительностью звонка **только по принятым звонкам** за каждый часовой интервал:

```
AHT (сек) = Сумма длительностей принятых звонков в данном часу
             ─────────────────────────────────────────────────
             Количество принятых звонков в данном часу
```

Поддерживаемые форматы длительности в файле:
- `ЧЧ:ММ:СС` (например, `00:03:45` = 225 секунд)
- `ММ:СС` (например, `03:45` = 225 секунд)
- Число в секундах (например, `225`)

---

## 👥 Required FTE — необходимое количество операторов

Показывает, **сколько операторов теоретически нужно** для обработки фактического объёма звонков при данном AHT:

```
Required FTE = Принятые звонки (за час) × AHT (сек)
               ──────────────────────────────────────
                           3 600 сек
```

> Делитель 3 600 — это количество секунд в одном часе.  
> Например: 40 звонков × 180 сек AHT / 3600 = **2.0 FTE**

---

## 📊 Occupancy % — загрузка (занятость) операторов

Показывает, какую долю рабочего времени операторы фактически тратили на звонки:

```
Occupancy % = Принятые звонки × AHT (сек)
              ────────────────────────────── × 100%
              Операторов (факт) × 3 600 сек
```

> Например: 40 звонков × 180 сек / (3 оператора × 3600 сек) = **66.7%**

### Интерпретация значений Occupancy:

| Цвет | Диапазон | Статус | Комментарий |
|------|----------|--------|-------------|
| 🔴 Красный | > 90% | Перегрузка | Операторы не справляются, очередь растёт |
| 🟡 Жёлтый | 70–90% | Высокая нагрузка | Рабочий режим, но близко к пределу |
| 🟢 Зелёный | 40–70% | Норма | Оптимальная загрузка |
| 🔵 Синий | < 40% | Недозагруженность | Операторов больше, чем нужно |

---

## 👤 Actual Operators (фактическое количество операторов)

В каждом часовом интервале считается количество **уникальных операторов**, у которых был хотя бы один звонок (принятый или потерянный):

```
Actual Operators = COUNT DISTINCT (имя оператора) за данный час и дату
```

Пустые значения, символ `+` и `NaN` исключаются из подсчёта.

---

## 📅 FTE за период (по оператору)

Для оценки нагрузки конкретного оператора за весь период:

```
% принятых оператора = Принятые звонки оператора
                        ─────────────────────────────────────── × 100%
                        (Принятые + Потерянные) оператора
```

---

## 🔍 Примечания

- Все расчёты ведутся **по каждому часовому интервалу отдельно** (00:00–01:00, 01:00–02:00, … 23:00–00:00)
- Если в файле нет колонки с длительностью — AHT, Required FTE и Occupancy не рассчитываются
- Строки с пустой датой или временем исключаются из расчётов
- Required FTE и Occupancy считаются только для часов, где есть хотя бы один принятый звонок с известной длительностью
    """)

if not uploaded_file:
    st.info("👆 Загрузите файл .xls или .xlsx для начала работы")

    with st.expander("📖 Инструкция по использованию"):
        st.markdown("""
        ### Как использовать:

        1. **Загрузите файл** от менеджера (.xls или .xlsx)

        2. **Приложение автоматически**:
           - Определит колонки с датой, временем, результатом и оператором
           - Подсчитает потерянные и принятые звонки по каждому часу
           - Посчитает количество операторов в каждый час
           - Рассчитает итоги, AHT, Required FTE и Occupancy %

        3. **Экспортируйте результат**:
           - Скачайте как Excel файл
           - Или отправьте в Google Sheets

        ### Формат времени:
        Отчёт группирует звонки по 24 часовым интервалам (00:00–01:00, 01:00–02:00, и т.д.)

        ### Для расчёта FTE и нагрузки:
        Убедитесь, что в файле есть колонка с длительностью звонка (в секундах или формате ЧЧ:ММ:СС).
        Подробнее — в разделе «📐 Методология расчётов» ниже.
        """)