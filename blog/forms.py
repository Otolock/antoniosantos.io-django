from django import forms


class SubscribeForm(forms.Form):
    email = forms.EmailField()


class CommentForm(forms.Form):
    author_name = forms.CharField(
        required=False,
        max_length=80,
        label="Name",
    )
    author_email = forms.EmailField(
        required=False,
        label="Email",
        help_text="Optional. It won't be published.",
    )
    body = forms.CharField(
        max_length=2000,
        label="Comment",
        widget=forms.Textarea(attrs={"rows": 6}),
    )
    website = forms.CharField(
        required=False,
        label="Website",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "tabindex": "-1",
            }
        ),
    )

    @property
    def honeypot_filled(self):
        return bool(self.data.get("website", "").strip())
