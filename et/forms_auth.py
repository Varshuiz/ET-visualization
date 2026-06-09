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


_REGION_SELECT_ATTRS = {"class": "region-form-select"}


class FarmProfileForm(forms.Form):
    farm_name = forms.CharField(max_length=200, label="Location name")
    province = forms.ChoiceField(choices=[], widget=forms.Select(attrs=_REGION_SELECT_ATTRS))
    city = forms.ChoiceField(choices=[], widget=forms.Select(attrs=_REGION_SELECT_ATTRS))
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
        city = (self.cleaned_data.get("city") or "").strip()
        if not city:
            raise forms.ValidationError("Select a city.")
        return city

    def clean(self):
        cleaned = super().clean()
        province = cleaned.get("province")
        city = cleaned.get("city")
        if not province or not city:
            return cleaned
        from .location_services import aquacrop_cities_for_province

        allowed = set(aquacrop_cities_for_province(province))
        posted_choices = {value for value, _ in self.fields["city"].choices if value}
        if city not in allowed and city not in posted_choices:
            self.add_error("city", "Select a city from the list.")
        return cleaned
