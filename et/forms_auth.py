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


class ForgotPasswordForm(forms.Form):
    email = forms.EmailField()


PASSWORD_TOO_SHORT = "Password is too short. Use at least 8 characters."
PASSWORDS_DONT_MATCH = "Passwords do not match."
RESET_LINK_INVALID = "This reset link is invalid or has expired. Request a new one."


class NewPasswordForm(forms.Form):
    password = forms.CharField(
        label="New password",
        widget=forms.PasswordInput,
        min_length=8,
        error_messages={
            "required": "Enter a new password.",
            "min_length": PASSWORD_TOO_SHORT,
        },
    )
    password_confirm = forms.CharField(
        label="Confirm password",
        widget=forms.PasswordInput,
        error_messages={"required": "Confirm your new password."},
    )

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password")
        p2 = cleaned.get("password_confirm")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError(PASSWORDS_DONT_MATCH)
        return cleaned


class ChangePasswordForm(NewPasswordForm):
    """New password for a user who is already signed in."""


class ResetPasswordForm(NewPasswordForm):
    access_token = forms.CharField(required=False, max_length=4096, widget=forms.HiddenInput)
    refresh_token = forms.CharField(required=False, max_length=4096, widget=forms.HiddenInput)
    token_hash = forms.CharField(required=False, max_length=512, widget=forms.HiddenInput)
    auth_code = forms.CharField(required=False, max_length=2048, widget=forms.HiddenInput)

    def clean(self):
        cleaned = super().clean()
        access = (cleaned.get("access_token") or "").strip()
        refresh = (cleaned.get("refresh_token") or "").strip()
        token_hash = (cleaned.get("token_hash") or "").strip()
        auth_code = (cleaned.get("auth_code") or "").strip()
        cleaned["access_token"] = access
        cleaned["refresh_token"] = refresh
        cleaned["token_hash"] = token_hash
        cleaned["auth_code"] = auth_code

        if not cleaned.get("password"):
            return cleaned
        if token_hash or auth_code:
            return cleaned
        if access and refresh and access.count(".") == 2:
            return cleaned
        raise forms.ValidationError(RESET_LINK_INVALID)


_REGION_SELECT_ATTRS = {"class": "region-form-select"}


_REGION_NUMBER_ATTRS = {"class": "region-form-select", "step": "0.0001"}


class FarmProfileForm(forms.Form):
    farm_name = forms.CharField(max_length=200, label="Location name")
    location_input_mode = forms.ChoiceField(
        choices=[("city", "City/Town"), ("coordinates", "Coordinates")],
        initial="city",
        widget=forms.HiddenInput(attrs={"id": "location_input_mode"}),
    )
    province = forms.ChoiceField(choices=[], widget=forms.Select(attrs=_REGION_SELECT_ATTRS))
    city = forms.ChoiceField(choices=[], required=False, widget=forms.Select(attrs=_REGION_SELECT_ATTRS))
    latitude = forms.DecimalField(
        max_digits=10,
        decimal_places=7,
        required=False,
        label="Latitude",
        widget=forms.NumberInput(attrs=_REGION_NUMBER_ATTRS),
    )
    longitude = forms.DecimalField(
        max_digits=10,
        decimal_places=7,
        required=False,
        label="Longitude",
        widget=forms.NumberInput(attrs=_REGION_NUMBER_ATTRS),
    )
    area_hectares = forms.DecimalField(
        max_digits=12,
        decimal_places=4,
        min_value=0,
        required=False,
        label="Area (hectares)",
    )
    crop_type = forms.CharField(max_length=80, required=False, label="Crop")
    soil_type = forms.ChoiceField(
        choices=[],
        required=False,
        label="Soil type",
        widget=forms.Select(attrs=_REGION_SELECT_ATTRS),
    )

    def __init__(self, *args, city_choices=None, **kwargs):
        super().__init__(*args, **kwargs)
        from .aquacrop_simulator import AquaCropSimulator
        from .location_services import aquacrop_province_choices

        self.fields["province"].choices = aquacrop_province_choices()
        cities = city_choices or []
        self.fields["city"].choices = [("", "Select city")] + [(name, name) for name in cities]
        soils = list(AquaCropSimulator.SOIL_TYPES.keys())
        self.fields["soil_type"].choices = [("", "Select soil type")] + [(name, name) for name in soils]

    def clean_city(self):
        mode = (self.data.get("location_input_mode") or "city").strip().lower()
        if mode == "coordinates":
            return (self.cleaned_data.get("city") or "").strip()
        city = (self.cleaned_data.get("city") or "").strip()
        if not city:
            raise forms.ValidationError("Select a city.")
        return city

    def clean(self):
        cleaned = super().clean()
        mode = (cleaned.get("location_input_mode") or "city").strip().lower()
        province = cleaned.get("province")
        if mode == "coordinates":
            lat = cleaned.get("latitude")
            lon = cleaned.get("longitude")
            if lat is None or lon is None:
                raise forms.ValidationError("Enter both latitude and longitude, or pick a point on the map.")
            from .location_services import validate_coordinates_in_province

            try:
                validate_coordinates_in_province(float(lat), float(lon), province or "")
            except ValueError as exc:
                raise forms.ValidationError(str(exc)) from exc
            return cleaned

        city = cleaned.get("city")
        if not province or not city:
            return cleaned
        from .location_services import aquacrop_cities_for_province

        allowed = set(aquacrop_cities_for_province(province))
        posted_choices = {value for value, _ in self.fields["city"].choices if value}
        if city not in allowed and city not in posted_choices:
            self.add_error("city", "Select a city from the list.")
        return cleaned
