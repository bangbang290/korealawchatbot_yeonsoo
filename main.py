# 필요한 모듈들을 가져옵니다.
import streamlit as st
import google.generativeai as genai
from dotenv import load_dotenv
import os
import json
import asyncio
import concurrent.futures
from pathlib import Path

# 로컬 유틸리티 및 API 모듈을 가져옵니다.
from pdf_json import convert_pdf_to_json, validate_json_structure, preview_json_data, download_json_file
from lawapi import LawAPI, convert_law_data_to_chatbot_format
from adminapi import AdminAPI, convert_admin_rule_data_to_chatbot_format

# 분리된 핵심 로직 함수들을 utils.py에서 가져옵니다.
from utils import (
    process_single_file,
    process_json_data,
    gather_agent_responses,
    get_head_agent_response
)

# --- 환경 변수 및 Gemini API 설정 ---
load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
LAW_API_KEY = os.getenv('LAW_API_KEY')
ADMIN_API_KEY = os.getenv('ADMIN_API_KEY')
genai.configure(api_key=GOOGLE_API_KEY)

# Streamlit 페이지 설정
st.set_page_config(
    page_title="법령 통합 챗봇 (PDF 지원 + API 검색)",
    page_icon="📚",
    layout="wide"
)

# --- 세션 상태 초기화 ---
if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []
if 'law_data' not in st.session_state:
    st.session_state.law_data = {}
if 'embedding_data' not in st.session_state:
    st.session_state.embedding_data = {}
if 'event_loop' not in st.session_state:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    st.session_state.event_loop = loop
if 'converted_files' not in st.session_state:
    st.session_state.converted_files = {}
if 'api_downloaded_laws' not in st.session_state:
    st.session_state.api_downloaded_laws = {}
if 'api_downloaded_admins' not in st.session_state:
    st.session_state.api_downloaded_admins = {}
# 새로 추가: 수집된 법률 데이터 통합 관리
if 'collected_laws' not in st.session_state:
    st.session_state.collected_laws = {}  # {name: {'type': 'pdf/law_api/admin_api', 'data': json_data}}

# --- 함수 정의 ---
def start_new_chat():
    """새 대화를 시작하는 함수"""
    st.session_state.chat_history = []
    st.success("새 대화가 시작되었습니다!")
    st.rerun()

def add_to_collected_laws(name, data_type, json_data):
    """수집된 법률 데이터에 추가하는 함수"""
    st.session_state.collected_laws[name] = {
        'type': data_type,
        'data': json_data
    }

def remove_from_collected_laws(name):
    """수집된 법률 데이터에서 제거하는 함수"""
    if name in st.session_state.collected_laws:
        del st.session_state.collected_laws[name]
        # 처리된 데이터에서도 제거
        if name in st.session_state.law_data:
            del st.session_state.law_data[name]
        if name in st.session_state.embedding_data:
            del st.session_state.embedding_data[name]

def clear_cache():
    """캐시를 삭제하는 함수"""
    # Streamlit 캐시 삭제
    st.cache_data.clear()
    st.cache_resource.clear()
    
    # 세션 상태 초기화 (데이터는 유지하고 캐시 관련만)
    if 'event_loop' in st.session_state:
        try:
            st.session_state.event_loop.close()
        except:
            pass
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        st.session_state.event_loop = loop
    
    st.success("캐시가 삭제되었습니다!")
    st.rerun()

def process_all_collected_laws():
    """수집된 모든 법률 데이터를 처리하는 함수"""
    if not st.session_state.collected_laws:
        st.warning("처리할 법률 데이터가 없습니다.")
        return
    
    with st.spinner("수집된 법률 데이터를 처리하고 있습니다..."):
        st.session_state.law_data = {}
        st.session_state.embedding_data = {}
        
        for name, law_info in st.session_state.collected_laws.items():
            json_data = law_info['data']
            result = process_json_data(name, json_data)
            processed_name, vec, mat, chunks, chunk_count = result
            
            if vec is not None:
                st.session_state.law_data[processed_name] = "processed"
                st.session_state.embedding_data[processed_name] = (vec, mat, chunks)
                st.success(f"✅ {processed_name} 처리 완료 ({chunk_count}개 조항)")
            else:
                st.error(f"❌ {processed_name} 처리 실패")
        
        st.success("모든 법률 데이터 처리가 완료되었습니다!")

# --- UI: 사이드바 ---
st.title("📚 법령 통합 챗봇 (PDF 지원 + API 검색)")
st.markdown("PDF, JSON 파일 업로드 또는 법률 API를 통한 검색으로 정확한 법령 해석을 받을 수 있습니다.")

with st.sidebar:
    st.header("📁 법령 데이터 준비")
    
    # 탭으로 각 데이터 소스를 분리
    tab1, tab2, tab3 = st.tabs(["📄 파일 업로드", "⚖️ 법률 API", "📋 행정규칙 API"])
    
    # 탭 1: 파일 업로드
    with tab1:
        file_type = st.radio("파일 유형:", ["PDF 파일", "JSON 파일"], key="file_type")
        
        if file_type == "PDF 파일":
            uploaded_files = st.file_uploader("PDF 파일 업로드", type=['pdf'], accept_multiple_files=True, key="pdf_upload")
            if uploaded_files:
                if st.button("PDF → JSON 변환", type="primary", key="convert_pdf"):
                    with st.spinner("PDF 파일을 변환하고 있습니다..."):
                        for uploaded_file in uploaded_files:
                            file_name = uploaded_file.name.replace('.pdf', '')
                            json_data = convert_pdf_to_json(uploaded_file)
                            if json_data and validate_json_structure(json_data):
                                add_to_collected_laws(file_name, 'PDF 파일', json_data)
                                st.success(f"✅ {file_name} 변환 완료 ({len(json_data)}개 조문)")
                            else:
                                st.error(f"❌ {file_name} 변환 실패")
        
        else:  # JSON 파일
            uploaded_files = st.file_uploader("JSON 파일 업로드", type=['json'], accept_multiple_files=True, key="json_upload")
            if uploaded_files:
                if st.button("JSON 파일 추가", type="primary", key="add_json"):
                    for uploaded_file in uploaded_files:
                        file_name = uploaded_file.name.replace('.json', '')
                        try:
                            json_data = json.loads(uploaded_file.read().decode('utf-8'))
                            if validate_json_structure(json_data):
                                add_to_collected_laws(file_name, 'JSON 파일', json_data)
                                st.success(f"✅ {file_name} 추가 완료 ({len(json_data)}개 조문)")
                            else:
                                st.error(f"❌ {file_name} 구조 검증 실패")
                        except Exception as e:
                            st.error(f"❌ {file_name} 처리 실패: {str(e)}")
    
    # 탭 2: 법률 API
    with tab2:
        if not LAW_API_KEY:
            st.error("LAW_API_KEY 환경 변수가 설정되지 않았습니다.")
        else:
            search_method = st.radio("검색 방법:", ["단일 법령", "다중 법령"], key="law_search_method")
            
            if search_method == "단일 법령":
                law_query = st.text_input("검색할 법령명:", placeholder="예: 민법, 형법", key="single_law_query")
                if st.button("법령 다운로드 및 변환", type="primary", key="search_single_law") and law_query:
                    with st.spinner(f"'{law_query}' 검색 중..."):
                        try:
                            law_api = LawAPI(LAW_API_KEY)
                            law_data = law_api.download_law_as_json(law_query)
                            if law_data:
                                chatbot_data = convert_law_data_to_chatbot_format(law_data)
                                law_name = law_data.get("법령명_한글", law_query)
                                add_to_collected_laws(law_name, '법률 API', chatbot_data)
                                st.success(f"✅ '{law_name}' 검색 완료 ({len(chatbot_data)}개 조문)")
                            else:
                                st.error(f"'{law_query}' 검색 결과가 없습니다.")
                        except Exception as e:
                            st.error(f"검색 중 오류 발생: {str(e)}")
            
            else:  # 다중 법령
                law_queries = st.text_area("검색할 법령명들 (한 줄씩):", placeholder="민법\n형법\n근로기준법", key="multi_law_query")
                if st.button("다중 법령 다운로드 및 변환", type="primary", key="search_multi_law") and law_queries:
                    law_names = [name.strip() for name in law_queries.split('\n') if name.strip()]
                    if law_names:
                        with st.spinner(f"{len(law_names)}개 법령 검색 중..."):
                            try:
                                law_api = LawAPI(LAW_API_KEY)
                                results = law_api.batch_download_laws(law_names)
                                for law_name, law_data in results.items():
                                    chatbot_data = convert_law_data_to_chatbot_format(law_data)
                                    display_name = law_data.get("법령명_한글", law_name)
                                    add_to_collected_laws(display_name, '법률 API', chatbot_data)
                                if results:
                                    st.success(f"총 {len(results)}개 법령 검색 완료")
                                else:
                                    st.error("검색된 법령이 없습니다.")
                            except Exception as e:
                                st.error(f"검색 중 오류 발생: {str(e)}")
    
    # 탭 3: 행정규칙 API
    with tab3:
        if not ADMIN_API_KEY:
            st.error("ADMIN_API_KEY 환경 변수가 설정되지 않았습니다.")
        else:
            search_method = st.radio("검색 방법:", ["단일 행정규칙", "다중 행정규칙"], key="admin_search_method")
            
            if search_method == "단일 행정규칙":
                admin_query = st.text_input("검색할 행정규칙명:", placeholder="예: 행정절차법 시행령", key="single_admin_query")
                if st.button("행정규칙 다운로드 및 변환", type="primary", key="search_single_admin") and admin_query:
                    with st.spinner(f"'{admin_query}' 검색 중..."):
                        try:
                            admin_api = AdminAPI(ADMIN_API_KEY)
                            admin_data = admin_api.download_admin_rule_as_json(admin_query)
                            if admin_data:
                                chatbot_data = convert_admin_rule_data_to_chatbot_format(admin_data)
                                admin_name = admin_data.get("행정규칙명", admin_query)
                                add_to_collected_laws(admin_name, '행정규칙 API', chatbot_data)
                                st.success(f"✅ '{admin_name}' 검색 완료 ({len(chatbot_data)}개 조문)")
                            else:
                                st.error(f"'{admin_query}' 검색 결과가 없습니다.")
                        except Exception as e:
                            st.error(f"검색 중 오류 발생: {str(e)}")
            
            else:  # 다중 행정규칙
                admin_queries = st.text_area("검색할 행정규칙명들 (한 줄씩):", placeholder="행정절차법 시행령\n민원처리 규정", key="multi_admin_query")
                if st.button("다중 행정규칙 다운로드 및 변환", type="primary", key="search_multi_admin") and admin_queries:
                    admin_names = [name.strip() for name in admin_queries.split('\n') if name.strip()]
                    if admin_names:
                        with st.spinner(f"{len(admin_names)}개 행정규칙 검색 중..."):
                            try:
                                admin_api = AdminAPI(ADMIN_API_KEY)
                                results = admin_api.batch_download_admin_rules(admin_names)
                                for admin_name, admin_data in results.items():
                                    chatbot_data = convert_admin_rule_data_to_chatbot_format(admin_data)
                                    display_name = admin_data.get("행정규칙명", admin_name)
                                    add_to_collected_laws(display_name, '행정규칙 API', chatbot_data)
                                if results:
                                    st.success(f"총 {len(results)}개 행정규칙 검색 완료")
                                else:
                                    st.error("검색된 행정규칙이 없습니다.")
                            except Exception as e:
                                st.error(f"검색 중 오류 발생: {str(e)}")
    
    # 기존 코드의 "수집된 법률 데이터 관리" 섹션을 다음과 같이 수정하세요:

    # 수집된 법률 데이터 관리 섹션
    st.markdown("---")
    st.header("📊 수집된 법률 데이터 관리")
    
    if st.session_state.collected_laws:
        st.subheader("📋 현재 수집된 법률")
        
        # 데이터 타입별 아이콘
        type_icons = {
            'PDF 파일': '📄',
            'JSON 파일': '📝',
            '법률 API': '⚖️',
            '행정규칙 API': '📋'
        }
        
        for name, law_info in st.session_state.collected_laws.items():
            col1, col2, col3 = st.columns([3, 1, 1])  # 컬럼 3개로 변경
            with col1:
                icon = type_icons.get(law_info['type'], '📄')
                st.write(f"{icon} **{name}** ({law_info['type']})")
                st.caption(f"조문 수: {len(law_info['data'])}개")
            with col2:
                # JSON 다운로드 버튼 추가
                json_data = json.dumps(law_info['data'], ensure_ascii=False, indent=2)
                st.download_button(
                    label="💾",
                    data=json_data,
                    file_name=f"{name}.json",
                    mime="application/json",
                    key=f"download_{name}",
                    help="JSON 다운로드"
                )
            with col3:
                if st.button("🗑️", key=f"delete_{name}", help="삭제"):
                    remove_from_collected_laws(name)
                    st.rerun()
        
        st.markdown("---")
        
        # 전체 다운로드 버튼 추가
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("🔄 챗봇용 데이터 변환 (벡터 임베딩 생성)", type="primary", use_container_width=True):
                process_all_collected_laws()
        with col2:
            # 전체 JSON 다운로드 버튼 추가
            if st.button("📦 전체 JSON 다운로드", type="secondary", use_container_width=True):
                # 모든 법률 데이터를 하나의 JSON으로 합치기
                all_laws_data = {}
                for name, law_info in st.session_state.collected_laws.items():
                    all_laws_data[name] = {
                        'type': law_info['type'],
                        'data': law_info['data'],
                        'article_count': len(law_info['data'])
                    }
                
                combined_json = json.dumps(all_laws_data, ensure_ascii=False, indent=2)
                st.download_button(
                    label="💾 통합 JSON 다운로드",
                    data=combined_json,
                    file_name="통합_법률_데이터.json",
                    mime="application/json",
                    key="download_all_laws",
                    help="모든 법률 데이터를 하나의 JSON 파일로 다운로드"
                )
        with col3:
            if st.button("🗑️ 전체 데이터 삭제", type="secondary", use_container_width=True):
                if st.session_state.collected_laws:
                    st.session_state.collected_laws = {}
                    st.session_state.law_data = {}
                    st.session_state.embedding_data = {}
                    st.success("모든 데이터가 삭제되었습니다.")
                    st.rerun()
        
        # 통계 정보
        total_articles = sum(len(law_info['data']) for law_info in st.session_state.collected_laws.values())
        type_counts = {}
        for law_info in st.session_state.collected_laws.values():
            data_type = law_info['type']
            type_counts[data_type] = type_counts.get(data_type, 0) + 1
        
        st.info(f"총 {len(st.session_state.collected_laws)}개 법률, {total_articles}개 조문")
        for data_type, count in type_counts.items():
            st.caption(f"• {type_icons.get(data_type, '📄')} {data_type}: {count}개")
    
    else:
        st.info("아직 수집된 법률 데이터가 없습니다.")
        st.caption("위의 탭에서 파일을 업로드하거나 API로 검색하여 법률을 수집해보세요.")
    
    st.markdown("---")
    st.header("💬 대화 관리")
    if st.button("🔄 새 대화 시작", type="secondary", use_container_width=True):
        start_new_chat()
    # 캐시 삭제 버튼 추가
    if st.button("🗑️ 캐시 삭제", type="secondary", use_container_width=True):
        clear_cache()
    # 대화 수 표시
    if st.session_state.chat_history:
        st.info(f"현재 대화 수: {len([msg for msg in st.session_state.chat_history if msg['role'] == 'user'])}개")

# --- UI: 메인 ---
if st.session_state.law_data:
    st.info(f"현재 {len(st.session_state.law_data)}개의 법령이 처리되어 사용 가능합니다: {', '.join(st.session_state.law_data.keys())}")

for msg in st.session_state.chat_history:
    with st.chat_message(msg['role']):
        st.markdown(msg['content'])

if user_input := st.chat_input("질문을 입력하세요"):
    if not st.session_state.law_data:
        st.warning("먼저 사이드바에서 법령 데이터를 수집하고 처리해주세요.")
        st.stop()
    
    st.session_state.chat_history.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)
    
    with st.chat_message("assistant"):
        with st.spinner("답변 생성 중..."):
            history = "\n".join([f"{m['role']}: {m['content']}" for m in st.session_state.chat_history])
            
            try:
                # 1. 수정된 gather_agent_responses의 반환값들을 모두 받습니다.
                responses, original_query, similar_queries, expanded_keywords = st.session_state.event_loop.run_until_complete(
                    gather_agent_responses(
                        question=user_input,
                        history=history,
                        law_data=st.session_state.law_data,
                        embedding_data=st.session_state.embedding_data,
                        event_loop=st.session_state.event_loop
                    )
                )
                
                # 2. 쿼리 분석 과정을 expander 내에 출력합니다.
                with st.expander("🔍 쿼리 분석 과정 보기"):
                    st.markdown(f"**원본 질문:**")
                    st.info(original_query)
                    st.markdown("**생성된 유사 질문:**")
                    for q in similar_queries:
                        st.markdown(f"- {q}")
                    st.markdown(f"**추출된 키워드 및 유사어:**")
                    st.success(expanded_keywords)

                # 3. get_head_agent_response에는 기존과 같이 responses만 전달합니다.
                answer = get_head_agent_response(responses, user_input, history)
                st.markdown(answer)
                st.session_state.chat_history.append({"role": "assistant", "content": answer})

            except Exception as e:
                error_msg = f"답변 생성 중 오류가 발생했습니다: {str(e)}"
                st.error(error_msg)
                st.session_state.chat_history.append({"role": "assistant", "content": error_msg})