"""SchoolX Keyboards Package."""

from bot.keyboards.admin_kb import (
    get_admin_main_kb,
    get_back_to_menu_kb,
    get_doc_clear_confirm_kb,
    get_doc_confirm_delete_kb,
    get_docs_list_kb,
    get_users_list_kb,
)
from bot.keyboards.feedback_kb import (
    get_feedback_keyboard,
    get_feedback_voted_keyboard,
)
from bot.keyboards.user_kb import (
    get_user_clear_confirm_kb,
    get_user_doc_confirm_delete_kb,
    get_user_docs_list_kb,
)

__all__ = [
    "get_feedback_keyboard",
    "get_feedback_voted_keyboard",
    "get_admin_main_kb",
    "get_docs_list_kb",
    "get_doc_confirm_delete_kb",
    "get_doc_clear_confirm_kb",
    "get_users_list_kb",
    "get_back_to_menu_kb",
    "get_user_docs_list_kb",
    "get_user_doc_confirm_delete_kb",
    "get_user_clear_confirm_kb",
]
