"""Local Windows-only credential registration; no network or Discord posting."""
import tkinter as tk
from tkinter import messagebox, ttk


class RegistrationWindow:
    def __init__(self, root, references, labels, validate, exists, save):
        self.root=root;self.references=references;self.labels=labels
        self.validate=validate;self.exists=exists;self.save_credential=save
        self.destination=tk.StringVar(root,value=labels['thailand_news'])
        self.value=tk.StringVar(root);self.show=tk.BooleanVar(root,value=False)
        self.input_status=tk.StringVar(root);self.vault_status=tk.StringVar(root)
        self.result=tk.StringVar(root)
        root.title('Ariadne — Discord webhook registration')
        root.minsize(560,330);root.resizable(True,False)
        root.protocol('WM_DELETE_WINDOW',self.cancel)
        root.report_callback_exception=lambda *_: self.result.set('The action could not be completed. Check the stored credential status.')
        frame=ttk.Frame(root,padding=20);frame.pack(fill='both',expand=True)
        frame.columnconfigure(0,weight=1)
        ttk.Label(frame,text='Discord webhook registration',font=('Segoe UI',14,'bold')).grid(row=0,column=0,columnspan=2,sticky='w')
        ttk.Label(frame,text='Destination').grid(row=1,column=0,sticky='w',pady=(14,2))
        selector=ttk.Combobox(frame,textvariable=self.destination,state='readonly',values=[labels['thailand_news'],labels['ai_noise']])
        selector.grid(row=2,column=0,columnspan=2,sticky='ew');selector.bind('<<ComboboxSelected>>',self.changed_destination)
        ttk.Label(frame,textvariable=self.vault_status).grid(row=3,column=0,columnspan=2,sticky='w',pady=(4,0))
        ttk.Label(frame,text='Webhook URL').grid(row=4,column=0,sticky='w',pady=(12,2))
        self.entry=ttk.Entry(frame,textvariable=self.value,show='•');self.entry.grid(row=5,column=0,sticky='ew',padx=(0,8))
        ttk.Button(frame,text='Paste from Clipboard',command=self.paste).grid(row=5,column=1,sticky='ew')
        ttk.Checkbutton(frame,text='Show URL',variable=self.show,command=self.toggle_visibility).grid(row=6,column=0,columnspan=2,sticky='w',pady=(5,0))
        ttk.Label(frame,textvariable=self.input_status,wraplength=570).grid(row=7,column=0,columnspan=2,sticky='w',pady=(5,0))
        ttk.Label(frame,textvariable=self.result,wraplength=570).grid(row=8,column=0,columnspan=2,sticky='w',pady=(7,0))
        actions=ttk.Frame(frame);actions.grid(row=9,column=0,columnspan=2,sticky='e',pady=(15,0))
        ttk.Button(actions,text='Cancel',command=self.cancel).pack(side='right')
        ttk.Button(actions,text='Save Credential',command=self.save).pack(side='right',padx=(0,8))
        ttk.Button(actions,text='Check Stored Status',command=self.check_stored).pack(side='right',padx=(0,8))
        self.value.trace_add('write',lambda *_:self.validate_input())
        self.validate_input();self.check_stored()

    def selected_key(self):
        return next((key for key in self.references if self.labels[key]==self.destination.get()),None)

    def changed_destination(self,_event=None):
        self.result.set('');self.validate_input();self.check_stored()

    def validate_input(self):
        key=self.selected_key();label=self.labels.get(key,'No destination')
        value=self.value.get().strip()
        if not value:
            self.input_status.set(f'{label}: no URL received yet.');return False
        try:self.validate(value)
        except ValueError:
            self.input_status.set(f'{label}: URL received, but it is not a recognised Discord webhook URL.');return False
        self.input_status.set(f'{label}: URL received and recognised as a Discord webhook URL.');return True

    def paste(self):
        try:value=self.root.clipboard_get()
        except tk.TclError:
            self.input_status.set('Clipboard is empty or does not contain text. Nothing was pasted.');return
        self.value.set(value.strip());self.result.set('')
        if not value.strip():self.input_status.set('Clipboard text is empty. Nothing was pasted.')

    def toggle_visibility(self):
        self.entry.configure(show='' if self.show.get() else '•')

    def check_stored(self):
        key=self.selected_key()
        if not key:self.vault_status.set('Select a destination.');return None
        try:found=self.exists(self.references[key])
        except (OSError,ValueError):
            self.vault_status.set(f'{self.labels[key]}: unable to check Windows Credential Manager.');return None
        self.vault_status.set(f'{self.labels[key]}: '+('credential is stored.' if found else 'no credential is stored.'))
        return found

    def save(self):
        key=self.selected_key()
        if not key:self.result.set('Select a destination.');return
        if not self.validate_input():self.result.set('Enter a valid webhook URL before saving.');return
        found=self.check_stored()
        if found is None:self.result.set('Cannot safely check existing credentials. Nothing was saved.');return
        if found and not messagebox.askyesno('Replace stored credential?',f'A credential is already stored for {self.labels[key]}. Replace it?',parent=self.root):
            self.result.set('Existing credential kept. Nothing was changed.');return
        try:self.save_credential(self.references[key],self.value.get().strip())
        except (OSError,ValueError) as exc:
            number=getattr(exc,'winerror',None) or (exc.errno if isinstance(exc,OSError) else None)
            if number==5:detail='Windows denied access. Use your usual Windows account and check Credential Manager permissions.'
            elif number:detail=f'Windows Credential Manager returned error {int(number)}. Check that the credential service is available.'
            else:detail='Windows Credential Manager could not save the credential. Check that its service is available.'
            self.result.set(detail);return
        # Clear the field immediately after saving, including when Show URL was selected.
        self.value.set('');self.show.set(False);self.toggle_visibility()
        if self.check_stored() is True:self.result.set(f'{self.labels[key]}: successfully stored in Windows Credential Manager.')
        else:self.result.set(f'{self.labels[key]}: save was attempted, but storage could not be confirmed. Check Credential Manager.')

    def cancel(self):
        self.value.set('');self.root.destroy()


def open_registration(references,labels,validate,exists,save):
    root=tk.Tk()
    RegistrationWindow(root,references,labels,validate,exists,save)
    root.mainloop()
