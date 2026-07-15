from django import forms

from .models import Note


class SubscribeForm(forms.Form):
    email = forms.EmailField()


class QuickNoteForm(forms.ModelForm):
    class Meta:
        model = Note
        fields = ["body", "tags"]
        widgets = {
            "body": forms.Textarea(
                attrs={
                    "class": "markdown-editor",
                    "data-markdown-editor": "true",
                    "spellcheck": "true",
                    "autocapitalize": "sentences",
                    "placeholder": "What’s happening? Markdown is welcome…",
                }
            ),
            "tags": forms.CheckboxSelectMultiple,
        }
