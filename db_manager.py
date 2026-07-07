"""
Interactive Database Manager TUI
Requires: pip install rich questionary
"""

import sys
import json
import contextlib
from sqlalchemy import inspect, text
from sqlalchemy.orm import Session
import questionary
from prompt_toolkit.styles import Style
from rich.console import Console
from rich.table import Table
from rich import print as rprint

from app.core.db import engine, Base

console = Console()

# TUI 스타일 정의: 선택된 항목을 흰색 배경에 검은 글씨로 반전
tui_style = Style([
    ('qmark', 'fg:#5f87d7 bold'),
    ('question', 'bold'),
    ('answer', 'fg:#ffaf00 bold'),
    ('pointer', 'fg:#ffffff bold'),
    ('highlighted', 'fg:#000000 bg:#ffffff bold'),
    ('selected', 'fg:#ffffff bg:#000000 bold'),
    ('separator', 'fg:#cccccc'),
    ('instruction', ''),
    ('text', ''),
    ('disabled', 'fg:#858585 italic')
])

@contextlib.contextmanager
def alternate_screen():
    # \033[?1049h : Use Alternate Screen Buffer
    sys.stdout.write("\033[?1049h")
    sys.stdout.flush()
    try:
        yield
    finally:
        # \033[?1049l : Switch back to Main Screen Buffer
        sys.stdout.write("\033[?1049l")
        sys.stdout.flush()

def pause():
    console.print("\n[dim]메뉴로 돌아가려면 Enter 키를 누르세요...[/dim]")
    input()

def get_tables():
    inspector = inspect(engine)
    return inspector.get_table_names()

def view_tables_list():
    console.clear()
    tables = get_tables()
    if not tables:
        console.print("[yellow]데이터베이스에 테이블이 없습니다.[/yellow]")
        pause()
        return

    # 2열 배치를 위해 컬럼 4개 생성
    table_display = Table(title="데이터베이스 테이블 목록", show_lines=True)
    table_display.add_column("테이블 이름", style="cyan", no_wrap=True)
    table_display.add_column("레코드 수", style="magenta", justify="right")
    table_display.add_column("테이블 이름", style="cyan", no_wrap=True)
    table_display.add_column("레코드 수", style="magenta", justify="right")

    table_data = []
    with engine.connect() as conn:
        for t_name in tables:
            try:
                cnt = str(conn.execute(text(f'SELECT COUNT(*) FROM "{t_name}"')).scalar())
            except Exception as e:
                cnt = "Error"
            table_data.append((t_name, cnt))

    # 데이터를 2개씩 묶어서 행 추가
    for i in range(0, len(table_data), 2):
        row = list(table_data[i])
        if i + 1 < len(table_data):
            row.extend(list(table_data[i+1]))
        else:
            row.extend(["", ""])
        table_display.add_row(*row)
                
    console.print(table_display)
    pause()

def truncate_value(val, max_len=50):
    if val is None:
        return "[dim]NULL[/dim]"
    
    if isinstance(val, dict) or isinstance(val, list):
        val_str = json.dumps(val, ensure_ascii=False)
    else:
        val_str = str(val)
        
    val_str = val_str.replace('\n', ' ')
    if len(val_str) > max_len:
        return val_str[:max_len] + "..."
    return val_str

def view_table_content():
    console.clear()
    tables = get_tables()
    if not tables:
        console.print("[yellow]데이터베이스에 테이블이 없습니다.[/yellow]")
        pause()
        return
        
    selected_table = questionary.select(
        "조회할 테이블을 선택하세요:",
        choices=tables + ["[뒤로 가기]"],
        style=tui_style
    ).ask()

    if not selected_table or selected_table == "[뒤로 가기]":
        return

    limit_str = questionary.select(
        "몇 개의 레코드를 조회하시겠습니까?",
        choices=["10", "50", "100", "전체"],
        style=tui_style
    ).ask()

    if not limit_str:
        return

    limit_clause = "" if limit_str == "전체" else f"LIMIT {limit_str}"
    
    console.clear()
    with engine.connect() as conn:
        result = conn.execute(text(f'SELECT * FROM "{selected_table}" {limit_clause}'))
        columns = list(result.keys())
        rows = result.fetchall()

    if not columns:
        console.print(f"[yellow]{selected_table} 테이블에 컬럼이 없습니다.[/yellow]")
        pause()
        return

    table_display = Table(title=f"테이블 데이터: {selected_table}", show_lines=True)
    for col in columns:
        table_display.add_column(col, style="green")

    for row in rows:
        formatted_row = [truncate_value(v) for v in row]
        table_display.add_row(*formatted_row)

    # 데이터가 많을 때 스크롤이 가능하도록 임시로 메인 버퍼로 돌아감
    sys.stdout.write("\033[?1049l")
    sys.stdout.flush()

    console.print(table_display)
    console.print(f"[dim]총 {len(rows)}개의 레코드를 불러왔습니다.[/dim]")
    pause()

    # 다시 TUI(대체 버퍼)로 복귀
    sys.stdout.write("\033[?1049h")
    sys.stdout.flush()

def clear_data_truncate():
    console.clear()
    tables = get_tables()
    if not tables:
        console.print("[yellow]지울 테이블이 없습니다.[/yellow]")
        pause()
        return

    confirm = questionary.confirm(
        "[!] 정말 모든 테이블의 데이터를 비우시겠습니까? (테이블 구조는 남고 데이터만 날아갑니다. CASCADE 적용)",
        default=False,
        style=tui_style
    ).ask()

    if confirm:
        with engine.begin() as conn:
            for t in tables:
                conn.execute(text(f'TRUNCATE TABLE "{t}" CASCADE'))
        console.print("[bold green][v] 모든 테이블의 데이터가 성공적으로 삭제되었습니다![/bold green]")
    else:
        console.print("[dim]작업이 취소되었습니다.[/dim]")
    pause()

def drop_all_tables():
    console.clear()
    confirm = questionary.confirm(
        "[!] 정말 모든 테이블을 완전히 삭제하시겠습니까? (DB 스키마 전체 파괴)",
        default=False,
        style=tui_style
    ).ask()

    if confirm:
        console.print("[yellow]진행 중...[/yellow]")
        Base.metadata.drop_all(bind=engine)
        
        tables_left = get_tables()
        if tables_left:
            with engine.begin() as conn:
                for t in tables_left:
                    conn.execute(text(f'DROP TABLE IF EXISTS "{t}" CASCADE'))

        console.print("[bold red][X] 데이터베이스의 모든 테이블이 완전히 삭제되었습니다![/bold red]")
    else:
        console.print("[dim]작업이 취소되었습니다.[/dim]")
    pause()

def main():
    with alternate_screen():
        while True:
            console.clear()
            choice = questionary.select(
                "[DB Manager TUI] - 원하는 작업을 선택하세요:",
                choices=[
                    "* 1. 테이블 목록 보기 (레코드 수)",
                    "* 2. 테이블 데이터 조회",
                    "* 3. DB 초기화 (데이터만 날리기 - TRUNCATE)",
                    "* 4. 모든 테이블 삭제 (완전 파괴 - DROP)",
                    "* 5. 종료"
                ],
                style=tui_style
            ).ask()

            if not choice or choice.startswith("* 5"):
                break
                
            elif choice.startswith("* 1"):
                view_tables_list()
            elif choice.startswith("* 2"):
                view_table_content()
            elif choice.startswith("* 3"):
                clear_data_truncate()
            elif choice.startswith("* 4"):
                drop_all_tables()

if __name__ == "__main__":
    try:
        import rich
        import questionary
    except ImportError:
        print("라이브러리가 설치되지 않았습니다. 실행 전 아래 명령어를 입력해주세요:")
        print("pip install rich questionary")
        sys.exit(1)
        
    main()
