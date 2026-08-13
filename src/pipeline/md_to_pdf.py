import os
import sys
import tkinter as tk
from tkinter import filedialog
import logging

# Add the src directory to the Python path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))

from src.pipeline.generator import Generator_Handler

# Configure logger
logger = logging.getLogger(__name__)


def select_file(title: str, filetypes: list[tuple[str, str] | tuple[str, str, str]] = None) -> str: # type: ignore
    """Open a file dialog to select a file.

    Args:
        title: The title of the file dialog.
        filetypes: A list of file types to filter. Defaults to markdown files.

    Returns:
        The selected file path or None if canceled.
    """
    logger.info(f"Opening file selection dialog: {title}")
    if filetypes is None:
        filetypes = [("Markdown files", "*.md")]

    root = tk.Tk()
    root.withdraw()  # Hide the main window
    root.attributes('-topmost', True)  # Bring the dialog to the front

    file_path = filedialog.askopenfilename(
        title=title, 
        filetypes=filetypes #type:ignore
    )

    root.destroy()
    logger.info(f"File selected: {file_path}")
    return file_path


def select_save_location(default_path: str) -> str:
    """Open a file dialog to select a save location.

    Args:
        default_path: The default path to use for the save dialog.

    Returns:
        The selected save path or None if canceled.
    """
    root = tk.Tk()
    root.withdraw()  # Hide the main window
    root.attributes('-topmost', True)  # Bring the dialog to the front

    logger.info("Opening save file dialog for PDF")
    save_path = filedialog.asksaveasfilename(
        title="Save PDF As",
        defaultextension=".pdf",
        initialfile=os.path.splitext(os.path.basename(default_path))[0],
        initialdir=os.path.dirname(default_path),
        filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
    )

    root.destroy()
    logger.info(f"Save location selected: {save_path}")
    return save_path


def select_document_type() -> str:
    """Open a modern UI dialog to select the document type (CV or Cover Letter).

    Returns:
        The selected document type ('cv' or 'cover letter') or None if canceled.
    """
    root = tk.Tk()
    root.withdraw()  # Hide the main window
    root.attributes('-topmost', True)  # Bring the dialog to the front
    root.configure(bg='#f0f0f0')

    # Create a modern dialog for document type selection
    dialog = tk.Toplevel(root)
    dialog.title("Select Document Type")
    dialog.geometry("350x300")
    dialog.configure(bg='#f0f0f0')
    dialog.resizable(False, True)

    # Style the dialog
    label = tk.Label(
        dialog, 
        text="Select the document type:", 
        font=('Segoe UI', 12, 'bold'),
        bg='#f0f0f0',
        fg='#333333'
    )
    label.pack(pady=(15, 10))

    # Frame for buttons
    button_frame = tk.Frame(dialog, bg='#f0f0f0')
    button_frame.pack(pady=10)

    def select_cv():
        nonlocal selected_type
        selected_type = "cv"
        dialog.destroy()
        root.destroy()

    def select_cover_letter():
        nonlocal selected_type
        selected_type = "cover letter"
        dialog.destroy()
        root.destroy()

    def on_cancel():
        nonlocal selected_type
        selected_type = None
        dialog.destroy()
        root.destroy()

    # Create three modern buttons: CV, Cover Letter, and Cancel
    cv_button = tk.Button(
        button_frame, 
        text="CV", 
        command=select_cv,
        font=('Segoe UI', 10),
        bg='#0078d4',
        fg='white',
        activebackground='#005a9e',
        activeforeground='white',
        relief=tk.FLAT,
        padx=20,
        pady=10,
        width=12
    )
    cv_button.pack(pady=5)

    cover_letter_button = tk.Button(
        button_frame, 
        text="Cover Letter", 
        command=select_cover_letter,
        font=('Segoe UI', 10),
        bg='#0078d4',
        fg='white',
        activebackground='#005a9e',
        activeforeground='white',
        relief=tk.FLAT,
        padx=20,
        pady=10,
        width=12
    )
    cover_letter_button.pack(pady=5)

    cancel_button = tk.Button(
        button_frame, 
        text="Cancel", 
        command=on_cancel,
        font=('Segoe UI', 10),
        bg='#cccccc',
        fg='#333333',
        activebackground='#aaaaaa',
        activeforeground='#333333',
        relief=tk.FLAT,
        padx=20,
        pady=10,
        width=12
    )
    cancel_button.pack(pady=5)

    # Add hover effects
    for button in [cv_button, cover_letter_button, cancel_button]:
        button.bind("<Enter>", lambda e, b=button: b.config(bg='#005a9e' if b['bg'] == '#0078d4' else '#bbbbbb'))
        button.bind("<Leave>", lambda e, b=button: b.config(bg='#0078d4' if b['bg'] == '#005a9e' else '#cccccc' if b['bg'] == '#bbbbbb' else '#cccccc'))

    selected_type = None
    dialog.wait_window()

    return selected_type # type: ignore


def convert_md_to_pdf():
    """Convert a markdown file to PDF using the Generator_Handler.

    This function:
    1. Asks the user for the document type (CV or Cover Letter) via UI.
    2. Asks the user to select a markdown file.
    3. Asks the user to select a save location.
    4. Converts the markdown file to PDF using the appropriate method.
    """
    # Ask for document type via UI
    doc_type = select_document_type()
    if not doc_type:
        print("No document type selected. Exiting.")
        return

    # Select markdown file
    md_file_path = select_file(
        title=f"Select {doc_type} markdown file",
        filetypes=[("Markdown files", "*.md"), ("All files", "*.*")]
    )

    if not md_file_path:
        print("No file selected. Exiting.")
        return

    # Select save location
    save_path = select_save_location(md_file_path)

    if not save_path:
        print("No save location selected. Exiting.")
        return

    # Read the markdown file
    with open(md_file_path, 'r', encoding='utf-8') as f:
        markdown_content = f.read()

    # Initialize Generator_Handler for PDF conversion
    # When llm is None, only PDF conversion methods are available
    generator = Generator_Handler(
        llm=None,
        language="en",
        llm_config={}
    )

    try:
        # Convert markdown to HTML based on document type
        if doc_type == "cv":
            html_content = generator.make_html_cv(markdown_content)
        else:  # cover letter
            html_content = generator.make_html_coverletter(markdown_content)

        # Convert HTML to PDF
        generator.make_pdf(html_content, save_path)

        print(f"Successfully converted {doc_type} to PDF: {save_path}")

    except Exception as e:
        print(f"Error converting {doc_type} to PDF: {e}")



if __name__ == "__main__":
    convert_md_to_pdf() 