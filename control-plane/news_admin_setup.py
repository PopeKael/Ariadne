"""Set a News Service admin password locally under the Ariadne Windows account."""
import os
import secrets
import hmac
import tkinter as tk
from tkinter import messagebox, ttk
import credential_store
from news_discord_admin import ADMIN_REF, password_hash

def save_password(password, confirmation, *, reference=ADMIN_REF, replace=False):
    """Save an explicitly confirmed password and verify that it can be read back."""
    if not isinstance(password, str) or password != confirmation or not 8 <= len(password) <= 256:
        raise ValueError('Passwords must match and contain 8–256 characters.')
    if credential_store.credential_exists(reference) and not replace:
        raise FileExistsError('An existing admin password requires replacement confirmation.')
    salt = secrets.token_hex(16)
    verifier = salt + ':' + password_hash(password, salt)
    credential_store.save_credential(reference, verifier)
    stored = credential_store.get_credential(reference, allow_environment=False)
    if not hmac.compare_digest(stored, verifier):
        raise OSError('The saved password could not be verified in Windows Credential Manager.')

def main():
    if os.name != 'nt': raise SystemExit('Windows is required.')
    root=tk.Tk(); root.title('Ariadne News Service admin setup'); root.resizable(False,False)
    frame=ttk.Frame(root,padding=20); frame.pack(fill='both',expand=True)
    ttk.Label(frame,text='Create the News Service admin password',font=('Segoe UI',12,'bold')).pack(anchor='w')
    ttk.Label(frame,text='Use at least 8 characters. This password unlocks Discord settings and posting.').pack(anchor='w',pady=8)
    first=tk.StringVar(); second=tk.StringVar()
    ttk.Label(frame,text='Password').pack(anchor='w'); ttk.Entry(frame,textvariable=first,show='•',width=55).pack(fill='x')
    ttk.Label(frame,text='Confirm password').pack(anchor='w'); ttk.Entry(frame,textvariable=second,show='•').pack(fill='x')
    status=tk.StringVar(); ttk.Label(frame,textvariable=status,wraplength=500).pack(anchor='w',pady=10)
    def save():
        password=first.get()
        if len(password)<8 or len(password)>256 or password != second.get():
            status.set('Passwords must match and contain 8–256 characters.'); return
        try:
            exists=credential_store.credential_exists(ADMIN_REF)
            if exists and not messagebox.askyesno('Replace admin password?', 'Replace the existing News Service admin password?',parent=root): return
            save_password(password, second.get(), replace=exists)
        except Exception:
            status.set('Unable to store the password verifier in Windows Credential Manager.'); return
        first.set('');second.set('');messagebox.showinfo('Saved','Admin password configured. You can now log in on the News Service page.',parent=root);root.destroy()
    ttk.Button(frame,text='Save admin password',command=save).pack(side='left')
    ttk.Button(frame,text='Cancel',command=root.destroy).pack(side='right')
    root.mainloop()
if __name__=='__main__': main()
