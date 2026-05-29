from django import forms


class RegisterForm(forms.Form):
    full_name = forms.CharField(
        max_length=120,
        required=True,
        label="Full name",
        help_text="Your name as it will appear on your dashboard.",
    )
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput, min_length=8)
    password_confirm = forms.CharField(widget=forms.PasswordInput, label="Confirm password")

    def clean_full_name(self):
        name = (self.cleaned_data.get("full_name") or "").strip()
        if len(name) < 2:
            raise forms.ValidationError("Please enter your full name (at least 2 characters).")
        return name

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("password_confirm")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("Passwords do not match.")
        return cleaned


class LoginForm(forms.Form):
    email = forms.EmailField()
    password = forms.CharField(widget=forms.PasswordInput)


class FarmProfileForm(forms.Form):
    farm_name = forms.CharField(max_length=200)
    province = forms.CharField(max_length=80)
    city = forms.CharField(max_length=120)
    area_hectares = forms.DecimalField(
        max_digits=12,
        decimal_places=4,
        min_value=0,
        required=False,
        label="Area (hectares)",
    )
    crop_type = forms.CharField(max_length=80, required=False)
    irrigation_type = forms.CharField(max_length=80, required=False)
